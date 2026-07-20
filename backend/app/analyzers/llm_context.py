"""
v1.7.4: сборка агрегированного контекста аккаунта для LLM-анализа.

Вынесено из llm_analyzer.py: логика разрослась после разбора боевых данных
gto365, где выяснилось, что модель рассуждает на неполной картине и делает
уверенные, но неверные выводы (предложила резать кампанию Hardox как нулевую,
хотя та давала 5 БП из 14 по кабинету).

Контекст отвечает на вопрос "с чем сравнивать" и состоит из пяти блоков:
  attribution_quality — насколько полны данные (сколько заявок вообще
                        привязано к ключам). Без него модель принимает
                        отсутствие crm_* у ключа за отсутствие заявок;
  account_totals      — итоги аккаунта; заявки берутся из CRM целиком,
                        а не только сматченные на ключевые слова;
  benchmarks          — медианы с явным размером выборки (медиана по двум
                        наблюдениям — шум, а не бенчмарк);
  campaigns           — разрез по кампаниям с ФАКТИЧЕСКИМИ заявками;
  long_tail           — сводка по малокликовым ключам;
  top_search_queries  — сырой спрос.
"""
from datetime import datetime, timedelta

from sqlalchemy import select, and_, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Keyword, AdGroup, Campaign, Lead, SearchQuery


async def build_context(db: AsyncSession, account_id: int, dataset: list[dict], period_days: int) -> dict:
    """v1.7.2: агрегированный контекст аккаунта поверх построчного датасета.

    Без него модель видела только плоский список ключей и не могла сказать
    "этот ключ дороже среднего по аккаунту в 3 раза" — отсюда и брались
    одиночные осторожные предложения. Теперь в тот же вызов передаются:
      - средние/медианные показатели аккаунта (бенчмарк для сравнения),
      - разрез по кампаниям (где горит бюджет и какая стратегия),
      - сводка по «длинному хвосту» малокликовых ключей,
      - топ поисковых запросов (сырой спрос — источник гипотез о минус-словах
        и о новых ключах, которых ещё нет в кабинете).
    Всё считается из уже загруженных данных, дополнительных вызовов LLM нет.
    """
    period_start = datetime.utcnow() - timedelta(days=period_days)

    def _safe_div(a, b):
        return round(a / b, 2) if b else None

    def _median(values: list[float]):
        vals = sorted(v for v in values if v is not None)
        if not vals:
            return None
        mid = len(vals) // 2
        return round(vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2, 2)

    # ── Итоги по аккаунту за период (бенчмарк, с которым модель сравнивает ключи)
    tot_clicks = sum(r["clicks"] for r in dataset)
    tot_spend = round(sum(r["spend_rub"] for r in dataset), 2)
    tot_leads = sum(r.get("crm_leads", 0) for r in dataset)
    tot_mql = sum(r.get("crm_mql", 0) for r in dataset)
    tot_sql = sum(r.get("crm_sql", 0) for r in dataset)

    account_totals = {
        "keywords_with_traffic": len(dataset),
        "clicks": tot_clicks,
        "spend_rub": tot_spend,
        "crm_leads": tot_leads,
        "crm_mql": tot_mql,
        "crm_sql": tot_sql,
        "avg_cpc_rub": _safe_div(tot_spend, tot_clicks),
        "cpl_rub": _safe_div(tot_spend, tot_leads),
        "cost_per_mql_rub": _safe_div(tot_spend, tot_mql),
        "cost_per_sql_rub": _safe_div(tot_spend, tot_sql),
        "cr_click_to_lead_pct": round(tot_leads / tot_clicks * 100, 2) if tot_clicks else None,
        "cr_lead_to_mql_pct": round(tot_mql / tot_leads * 100, 1) if tot_leads else None,
        "cr_mql_to_sql_pct": round(tot_sql / tot_mql * 100, 1) if tot_mql else None,
    }
    # v1.7.4: медиана по двум наблюдениям — это не бенчмарк, а шум. На
    # реальном прогоне "медиана аккаунта 519 ₽" была посчитана ровно по
    # двум ключам с SQL, и модель сделала на ней вывод "30 650 критически
    # дорого". Теперь медиана отдаётся только при достаточной выборке, а
    # размер выборки сообщается модели явно.
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

    # ── Разрез по кампаниям: где сосредоточен бюджет и какая там стратегия
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

    # ── v1.7.4: ФАКТИЧЕСКИЕ лиды по кампаниям, включая те, что не привязались
    #    к ключу. Раньше crm_leads кампании складывался только из лидов,
    #    сматченных на ключевые слова, — а таких доезжает около четверти.
    #    Из-за этого кампания с реальными заявками выглядела нулевой, и
    #    модель предлагала её резать. Теперь берём правду из leads по
    #    campaign_id, а keyword-уровень оставляем отдельным полем.
    camp_leads_rows = await db.execute(
        select(
            Lead.campaign_id,
            func.count(Lead.id).label("leads"),
            func.sum(case((Lead.is_mql.is_(True), 1), else_=0)).label("mql"),
            func.sum(case((Lead.is_sql.is_(True), 1), else_=0)).label("sql"),
        )
        .where(and_(
            Lead.account_id == account_id,
            Lead.campaign_id.isnot(None),
            Lead.created_at >= period_start,
        ))
        .group_by(Lead.campaign_id)
    )
    leads_by_campaign = {
        r.campaign_id: {"leads": int(r.leads or 0), "mql": int(r.mql or 0), "sql": int(r.sql or 0)}
        for r in camp_leads_rows.all()
    }

    # Кампании, где есть заявки, но ни один ключ не попал в датасет, иначе
    # выпали бы из разреза совсем.
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
            }

    campaigns = []
    for camp_id, agg in campaigns_agg.items():
        fact = leads_by_campaign.get(camp_id)
        if fact:
            agg["crm_leads_by_keyword"] = agg["crm_leads"]
            agg["crm_leads"] = fact["leads"]
            agg["crm_mql"] = fact["mql"]
            agg["crm_sql"] = fact["sql"]
    for agg in campaigns_agg.values():
        agg["spend_rub"] = round(agg["spend_rub"], 2)
        agg["cost_per_sql_rub"] = _safe_div(agg["spend_rub"], agg["crm_sql"])
        agg["cpl_rub"] = _safe_div(agg["spend_rub"], agg["crm_leads"])
        agg["share_of_spend_pct"] = round(agg["spend_rub"] / tot_spend * 100, 1) if tot_spend else None
        campaigns.append(agg)
    campaigns.sort(key=lambda c: c["spend_rub"], reverse=True)

    # ── «Длинный хвост»: малокликовые ключи по отдельности ничего не значат,
    #    но в сумме могут съедать заметную долю бюджета без единой заявки.
    #    Эта сводка переживает урезание payload в llm_budget.fit_to_budget,
    #    даже если сами thin_data-строки из датасета вырезаны.
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

    # ── Сырой спрос: что люди реально вводили. Источник гипотез о минус-словах
    #    и о ключах, которых в кабинете ещё нет.
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

    # ── v1.7.4: качество атрибуции. Модель должна знать, НАСКОЛЬКО полны
    #    данные, на которых она рассуждает. Раньше несматченные лиды просто
    #    исчезали, и 2 дошедших SQL подавались как вся правда об аккаунте.
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
    total_leads_all = sum(v["leads"] for v in by_match.values())
    total_sql_all = sum(v["sql"] for v in by_match.values())
    kw_level_sql = sum(v["sql"] for k, v in by_match.items()
                       if k in ("ad_id", "search_query", "phrase"))

    attribution = {
        "leads_total_in_crm": total_leads_all,
        "sql_total_in_crm": total_sql_all,
        "by_match_method": by_match,
        "sql_attributed_to_keyword": kw_level_sql,
        "sql_keyword_coverage_pct": round(kw_level_sql / total_sql_all * 100, 1) if total_sql_all else None,
        "note": (
            "leads_total_in_crm / sql_total_in_crm — ВСЕ заявки за период. "
            "К конкретным ключевым словам привязана только часть (см. "
            "sql_keyword_coverage_pct). Разрез campaigns содержит фактические "
            "заявки по кампаниям и полнее построчных данных по ключам. "
            "Если покрытие низкое, НЕ делай вывод 'ключ/кампания не приносит "
            "заявок' из отсутствия crm_* в построчных данных — заявка могла "
            "быть, но не привязаться к ключу. В таких случаях опирайся на "
            "campaigns и говори об этом прямо в diagnostics."
        ),
    }

    # Итоги аккаунта по заявкам — из CRM целиком, а не только по сматченным
    account_totals["crm_leads"] = total_leads_all
    account_totals["crm_sql"] = total_sql_all
    account_totals["cpl_rub"] = _safe_div(tot_spend, total_leads_all)
    account_totals["cost_per_sql_rub"] = _safe_div(tot_spend, total_sql_all)
    account_totals["cr_click_to_lead_pct"] = (
        round(total_leads_all / tot_clicks * 100, 2) if tot_clicks else None
    )

    return {
        "period_days": period_days,
        "attribution_quality": attribution,
        "account_totals": account_totals,
        "benchmarks": benchmarks,
        "campaigns": campaigns,
        "long_tail": long_tail,
        "top_search_queries": top_search_queries,
    }
