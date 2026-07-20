"""
v1.7.5: сборка агрегированного контекста аккаунта для LLM-анализа.

Контекст отвечает на вопрос «с чем сравнивать» и состоит из блоков:
  data_freshness      — до какой даты есть данные Директа и CRM (v1.7.5);
  attribution_quality — насколько полны данные;
  account_totals      — итоги аккаунта (заявки из CRM целиком);
  benchmarks          — медианы с явным размером выборки;
  campaigns           — разрез по кампаниям с ФАКТИЧЕСКИМИ заявками;
  long_tail           — сводка по малокликовым ключам;
  top_search_queries  — сырой спрос.

ЧТО ПОЧИНЕНО В v1.7.5 (по итогам разбора «модель видит не ту статистику»):

  1. ПЕРЕСЧЁТ АТРИБУЦИИ ПЕРЕД АНАЛИЗОМ. Раньше разноска замораживалась
     на момент импорта CRM-файла. Файл почти всегда загружают раньше,
     чем досинхронизирован Директ → search_queries ещё пусты → keyword_id
     NULL навсегда. Это и была главная причина «заявки не разносятся».

  2. СМЕШЕНИЕ УРОВНЕЙ В account_totals. crm_leads брался из CRM целиком,
     а crm_mql/crm_sql оставались только по СМАТЧЕННЫМ на ключи заявкам.
     Модель делила одно на другое и получала CR lead→MQL вида 25% там,
     где реально около 100%. Теперь оба уровня отдаются отдельно
     и подписаны.

  3. cost_per_sql_rub = 0₽ вместо null у кампаний без открутки в периоде
     (деление 0 на N). Модель читала это как «бесплатные заявки».

  4. Заявки с ключом, но без campaign_id, добираются через джойн
     keyword → ad_group → campaign.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, and_, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Keyword, AdGroup, Campaign, Lead, SearchQuery, KeywordStat
from app.importers.lead_attribution import reattribute_account

logger = logging.getLogger(__name__)


async def build_context(db: AsyncSession, account_id: int, dataset: list[dict],
                        period_days: int, reattribute: bool = True) -> dict:
    """Агрегированный контекст аккаунта поверх построчного датасета."""
    period_start = datetime.utcnow() - timedelta(days=period_days)

    # v1.7.5: разноска заявок пересчитывается перед КАЖДЫМ анализом.
    # Дёшево (читает уже загруженные таблицы, без внешних вызовов), но
    # гарантирует, что модель видит актуальную атрибуцию, а не замороженную
    # на момент загрузки CRM-файла. Не валим анализ, если пересчёт упал.
    reattribution = None
    if reattribute:
        try:
            reattribution = await reattribute_account(db, account_id)
        except Exception as e:
            logger.warning(f"reattribute_account failed (account={account_id}): {e}")
            reattribution = {"error": str(e)}

    def _safe_div(a, b):
        """None, если делить не на что ИЛИ делимое нулевое.

        v1.7.5: прежняя версия проверяла только знаменатель и отдавала 0.0
        для кампаний без открутки — «заявки по 0₽» в глазах модели.
        """
        if not b or not a:
            return None
        return round(a / b, 2)

    def _median(values: list[float]):
        vals = sorted(v for v in values if v is not None)
        if not vals:
            return None
        mid = len(vals) // 2
        return round(vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2, 2)

    # ── Итоги по аккаунту за период
    tot_clicks = sum(r["clicks"] for r in dataset)
    tot_spend = round(sum(r["spend_rub"] for r in dataset), 2)
    kw_leads = sum(r.get("crm_leads", 0) for r in dataset)
    kw_mql = sum(r.get("crm_mql", 0) for r in dataset)
    kw_sql = sum(r.get("crm_sql", 0) for r in dataset)

    # ── Фактическая воронка из CRM за период — ВСЕ заявки, независимо от
    #    того, привязались ли они к ключу. v1.7.5: раньше целиком брался
    #    только leads, а mql/sql оставались keyword-уровневыми — воронка из
    #    двух разных источников давала бессмысленные CR.
    crm_tot_row = (await db.execute(
        select(
            func.count(Lead.id).label("leads"),
            func.sum(case((Lead.is_mql.is_(True), 1), else_=0)).label("mql"),
            func.sum(case((Lead.is_sql.is_(True), 1), else_=0)).label("sql"),
        ).where(and_(Lead.account_id == account_id, Lead.created_at >= period_start))
    )).one()
    crm_leads = int(crm_tot_row.leads or 0)
    crm_mql = int(crm_tot_row.mql or 0)
    crm_sql = int(crm_tot_row.sql or 0)

    account_totals = {
        "keywords_with_traffic": len(dataset),
        "clicks": tot_clicks,
        "spend_rub": tot_spend,
        # ВСЕ заявки из CRM за период
        "crm_leads": crm_leads,
        "crm_mql": crm_mql,
        "crm_sql": crm_sql,
        # Только те, что привязались к конкретным ключам — НЕ путать
        "crm_leads_by_keyword": kw_leads,
        "crm_mql_by_keyword": kw_mql,
        "crm_sql_by_keyword": kw_sql,
        "avg_cpc_rub": _safe_div(tot_spend, tot_clicks),
        "cpl_rub": _safe_div(tot_spend, crm_leads),
        "cost_per_mql_rub": _safe_div(tot_spend, crm_mql),
        "cost_per_sql_rub": _safe_div(tot_spend, crm_sql),
        "cr_click_to_lead_pct": round(crm_leads / tot_clicks * 100, 2) if tot_clicks else None,
        "cr_lead_to_mql_pct": round(crm_mql / crm_leads * 100, 1) if crm_leads else None,
        "cr_mql_to_sql_pct": round(crm_sql / crm_mql * 100, 1) if crm_mql else None,
        "note": (
            "crm_leads / crm_mql / crm_sql — ВСЕ заявки из CRM за период. "
            "Поля *_by_keyword — только те, что удалось привязать к конкретному "
            "ключевому слову. НЕ считай CR, деля показатели разных уровней "
            "друг на друга — все CR выше уже посчитаны корректно."
        ),
    }

    # ── Бенчмарки: медиана по двум наблюдениям — шум, а не бенчмарк.
    MIN_SAMPLE = 5

    def _median_with_n(values):
        vals = [v for v in values if v is not None]
        n = len(vals)
        return {"value": _median(vals) if n >= MIN_SAMPLE else None, "n": n}

    benchmarks = {
        "median_cost_per_sql_rub": _median_with_n([r.get("cost_per_sql_rub") for r in dataset]),
        "median_cost_per_mql_rub": _median_with_n([r.get("cost_per_mql_rub") for r in dataset]),
        "median_cpl_rub": _median_with_n([r.get("cpl_rub") for r in dataset]),
        "median_bounce_rate_pct": _median_with_n([r.get("bounce_rate_pct") for r in dataset]),
        "min_sample_size": MIN_SAMPLE,
        "note": (
            "Каждая медиана дана в виде {value, n}, где n — на скольких ключах она "
            f"построена. Если value=null, наблюдений меньше {MIN_SAMPLE} и медианы нет — "
            "НЕ придумывай её и не делай выводов вида 'дороже медианы в N раз'. "
            "При достаточном n сравнивай показатели ключа с медианой: отклонение в разы "
            "в любую сторону — повод для гипотезы."
        ),
    }

    # ── Разрез по кампаниям
    kw_ids = [r["keyword_id"] for r in dataset]
    camp_by_kw: dict[int, Campaign] = {}
    if kw_ids:
        rows = await db.execute(
            select(Keyword.id, Campaign)
            .join(AdGroup, Keyword.ad_group_id == AdGroup.id)
            .join(Campaign, AdGroup.campaign_id == Campaign.id)
            .where(Keyword.id.in_(kw_ids))
        )
        camp_by_kw = {kw_id: camp for kw_id, camp in rows.all()}

    campaigns_agg: dict[int, dict] = {}
    for r in dataset:
        camp = camp_by_kw.get(r["keyword_id"])
        if not camp:
            continue
        agg = campaigns_agg.setdefault(camp.id, {
            "campaign": camp.name,
            "strategy_type": camp.strategy_type,
            "bid_editable": camp.strategy_type == "MANUAL_CPC",
            "status": camp.status,
            "keywords": 0, "clicks": 0, "spend_rub": 0.0,
            "crm_leads": 0, "crm_mql": 0, "crm_sql": 0,
        })
        agg["keywords"] += 1
        agg["clicks"] += r["clicks"]
        agg["spend_rub"] += r["spend_rub"]
        agg["crm_leads"] += r.get("crm_leads", 0)
        agg["crm_mql"] += r.get("crm_mql", 0)
        agg["crm_sql"] += r.get("crm_sql", 0)

    # ── ФАКТИЧЕСКИЕ заявки по кампаниям: campaign_id либо напрямую,
    #    либо через keyword → ad_group → campaign (v1.7.5: второй путь добавлен
    #    на случай, если campaign_id у заявки почему-то остался пустым).
    effective_campaign = func.coalesce(Lead.campaign_id, AdGroup.campaign_id)
    camp_leads_rows = await db.execute(
        select(
            effective_campaign.label("campaign_id"),
            func.count(Lead.id).label("leads"),
            func.sum(case((Lead.is_mql.is_(True), 1), else_=0)).label("mql"),
            func.sum(case((Lead.is_sql.is_(True), 1), else_=0)).label("sql"),
        )
        .select_from(Lead)
        .outerjoin(Keyword, Lead.keyword_id == Keyword.id)
        .outerjoin(AdGroup, Keyword.ad_group_id == AdGroup.id)
        .where(and_(
            Lead.account_id == account_id,
            Lead.created_at >= period_start,
            effective_campaign.isnot(None),
        ))
        .group_by(effective_campaign)
    )
    leads_by_campaign = {
        r.campaign_id: {"leads": int(r.leads or 0), "mql": int(r.mql or 0), "sql": int(r.sql or 0)}
        for r in camp_leads_rows.all()
    }

    # Кампании с заявками, но без ключей в датасете — иначе выпали бы.
    missing_camp_ids = [cid for cid in leads_by_campaign if cid not in campaigns_agg]
    if missing_camp_ids:
        extra_q = await db.execute(select(Campaign).where(Campaign.id.in_(missing_camp_ids)))
        for camp in extra_q.scalars().all():
            campaigns_agg[camp.id] = {
                "campaign": camp.name,
                "strategy_type": camp.strategy_type,
                "bid_editable": camp.strategy_type == "MANUAL_CPC",
                "status": camp.status,
                "keywords": 0, "clicks": 0, "spend_rub": 0.0,
                "crm_leads": 0, "crm_mql": 0, "crm_sql": 0,
                "note": "Заявки есть, но ни один ключ кампании не попал в датасет за период",
            }

    campaigns = []
    for camp_id, agg in campaigns_agg.items():
        fact = leads_by_campaign.get(camp_id)
        if fact:
            agg["crm_leads_by_keyword"] = agg["crm_leads"]
            agg["crm_leads"] = fact["leads"]
            agg["crm_mql"] = fact["mql"]
            agg["crm_sql"] = fact["sql"]
        agg["spend_rub"] = round(agg["spend_rub"], 2)
        agg["cost_per_sql_rub"] = _safe_div(agg["spend_rub"], agg["crm_sql"])
        agg["cpl_rub"] = _safe_div(agg["spend_rub"], agg["crm_leads"])
        agg["share_of_spend_pct"] = round(agg["spend_rub"] / tot_spend * 100, 1) if tot_spend else None
        campaigns.append(agg)
    campaigns.sort(key=lambda c: c["spend_rub"], reverse=True)

    # ── «Длинный хвост»
    thin = [r for r in dataset if r.get("thin_data")]
    long_tail = {
        "keywords_count": len(thin),
        "clicks": sum(r["clicks"] for r in thin),
        "spend_rub": round(sum(r["spend_rub"] for r in thin), 2),
        "crm_leads": sum(r.get("crm_leads", 0) for r in thin),
        "crm_sql": sum(r.get("crm_sql", 0) for r in thin),
        "note": (
            "Ключи с 1-2 кликами (thin_data=true). По отдельности решение по ним "
            "принимать рано, но если они в сумме съедают заметную долю бюджета без "
            "заявок — это отдельная гипотеза уровня аккаунта, сформулируй её."
        ),
    }

    # ── Сырой спрос
    sq_rows = await db.execute(
        select(
            SearchQuery.query,
            SearchQuery.keyword_phrase,
            func.sum(SearchQuery.clicks).label("clicks"),
            func.sum(SearchQuery.spend).label("spend"),
            func.sum(SearchQuery.impressions).label("impressions"),
        )
        .where(and_(
            SearchQuery.account_id == account_id,
            SearchQuery.date >= period_start,
        ))
        .group_by(SearchQuery.query, SearchQuery.keyword_phrase)
        .order_by(func.sum(SearchQuery.spend).desc())
        .limit(60)
    )
    top_search_queries = [
        {
            "query": row.query,
            "matched_keyword": row.keyword_phrase,
            "impressions": int(row.impressions or 0),
            "clicks": int(row.clicks or 0),
            "spend_rub": round(float(row.spend or 0), 2),
        }
        for row in sq_rows.all()
    ]

    # ── Качество атрибуции
    attr_rows = await db.execute(
        select(
            Lead.matched_by,
            func.count(Lead.id).label("leads"),
            func.sum(case((Lead.is_sql.is_(True), 1), else_=0)).label("sql"),
        )
        .where(and_(Lead.account_id == account_id, Lead.created_at >= period_start))
        .group_by(Lead.matched_by)
    )
    by_match = {(r.matched_by or "unmatched"): {"leads": int(r.leads or 0), "sql": int(r.sql or 0)}
                for r in attr_rows.all()}
    total_sql_all = sum(v["sql"] for v in by_match.values())
    kw_level_sql = sum(v["sql"] for k, v in by_match.items()
                       if k in ("ad_id", "search_query", "phrase"))

    attribution = {
        "leads_total_in_crm": crm_leads,
        "sql_total_in_crm": crm_sql,
        "by_match_method": by_match,
        "sql_attributed_to_keyword": kw_level_sql,
        "sql_keyword_coverage_pct": round(kw_level_sql / total_sql_all * 100, 1) if total_sql_all else None,
        "leads_keyword_coverage_pct": round(kw_leads / crm_leads * 100, 1) if crm_leads else None,
        "reattribution_run": reattribution,
        "note": (
            "leads_total_in_crm / sql_total_in_crm — ВСЕ заявки за период. "
            "К конкретным ключевым словам привязана только часть (см. "
            "sql_keyword_coverage_pct / leads_keyword_coverage_pct). Разрез campaigns "
            "содержит фактические заявки по кампаниям и полнее построчных данных "
            "по ключам. Если покрытие низкое, НЕ делай вывод 'ключ/кампания не "
            "приносит заявок' из отсутствия crm_* в построчных данных — заявка могла "
            "быть, но не привязаться к ключу. В таких случаях опирайся на campaigns "
            "и говори об этом прямо в diagnostics. Блок reattribution_run показывает "
            "результат пересчёта разноски, выполненного перед этим анализом; "
            "matchers внутри него — на чём физически строился матчинг. Если там "
            "search_queries=0, причина низкого покрытия — несобранные данные "
            "Директа, а не отсутствие заявок."
        ),
    }

    # ── v1.7.5: свежесть данных. Если CRM-выгрузка обрывается раньше конца
    #    периода, последние дни выглядят как «трафик есть, заявок нет», и модель
    #    делает из этого вывод о падении конверсии. Это артефакт выгрузки.
    last_stat = (await db.execute(
        select(func.max(KeywordStat.date)).where(KeywordStat.account_id == account_id)
    )).scalar()
    last_lead = (await db.execute(
        select(func.max(Lead.created_at)).where(Lead.account_id == account_id)
    )).scalar()
    gap_days = None
    if last_stat and last_lead:
        gap_days = (last_stat.date() - last_lead.date()).days

    data_freshness = {
        "period_start": period_start.date().isoformat(),
        "period_end": datetime.utcnow().date().isoformat(),
        "last_direct_stat_date": last_stat.date().isoformat() if last_stat else None,
        "last_crm_lead_date": last_lead.date().isoformat() if last_lead else None,
        "crm_lag_days": gap_days,
        "note": (
            "Если crm_lag_days > 2, выгрузка CRM отстаёт от статистики Директа: "
            "последние дни периода содержат расход без заявок НЕ потому, что "
            "заявок нет, а потому что их ещё не выгрузили. Обязательно скажи об "
            "этом в diagnostics и не считай CPL/CR по этим дням как показательные."
        ),
    }

    return {
        "period_days": period_days,
        "data_freshness": data_freshness,
        "attribution_quality": attribution,
        "account_totals": account_totals,
        "benchmarks": benchmarks,
        "campaigns": campaigns,
        "long_tail": long_tail,
        "top_search_queries": top_search_queries,
    }
