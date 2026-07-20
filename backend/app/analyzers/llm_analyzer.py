"""
LLM-анализатор. В отличие от cr_analyzer.py (жёсткие пороги), здесь LLM
получает агрегированные данные по ключевым словам (Директ + CRM) и сам решает,
какие проблемы есть и что с ними делать.

v1.5.0: в датасет добавлен bid_editable (см. _build_dataset) — выяснилось на
real apply, что в этом кабинете нет ни одной кампании на MANUAL_CPC — для
авто-стратегий/ЕПК Yandex Direct API не принимает Keywords[].Bid вообще
("API error 8000: неизвестный параметр Bid") — это ограничение самого API, не баг.
_validate_change теперь жёстко отклоняет bid_raise/bid_lower для bid_editable=false даже
если модель всё равно их предложит — не полагаемся только на послушность промпту.

Пайплайн:
  1. _build_dataset()      — джойн keyword_stats + leads по keyword_id/utm_term,
                              агрегация в компактную таблицу (не сырые построчные данные).
  2. _build_context()      — v1.7.2: агрегаты по аккаунту/кампаниям/спросу поверх
                              построчного датасета — бенчмарк, с которым модель сравнивает ключи.
  3. llm_providers.call_llm() — вызов выбранного провайдера (Claude/Gemini/Groq/OpenRouter),
                              модель обязана вернуть строго типизированный список изменений.
  4. generate_suggestions() — валидация ответа модели (лимиты из config.py) и запись
                              в таблицу suggestions (общая с cr_analyzer, status=pending).

Так же, как и для cr_analyzer, safety-валидация лимитов ставки происходит на этапе
записи — модель не может напрямую менять кабинет.

v1.4.0: CRM-метрики в датасете переведены с "сделки/выручка" на воронку
MQL/SQL (см. app/importers/crm_importer.py) — для этого аккаунта модель
"продажа" не в приоритете, важны стоимость и конверсия в MQL/SQL.

v1.7.2 (глубина анализа): две причины, почему раньше модель возвращала
по одному-два предложения за запуск, устранены:
  1. в _build_dataset молча выбрасывались все ключи с clicks < 3 — в модель
     уходило ~19 строк из сотен. Теперь они помечаются thin_data=true, а не
     удаляются: по одному такому ключу решение принимать рано, но групповая
     закономерность по ним — самостоятельная гипотеза;
  2. модель видела только плоский список ключей без точки отсчёта — добавлен
     _build_context() с бенчмарками аккаунта, разрезом по кампаниям,
     сводкой по длинному хвосту и топом поисковых запросов.

v1.7.3: в summary пишется llm_payload_meta — сколько данных реально доехало до
модели после ужимания под лимиты провайдера (см. llm_budget.fit_to_budget).
Без этого урезание было бы молчаливым и неотличимым от "ИИ почему-то мало
нашёл" — а это разные диагнозы с разным лечением.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, and_, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.analyzers import llm_providers
from app.models.models import (
    Keyword, KeywordStat, AdGroup, Campaign, Lead, LeadStatus, SearchQuery,
    AnalysisResult, Suggestion, SuggestionStatus,
)

logger = logging.getLogger(__name__)


class LLMAnalyzer:
    def __init__(self, db: AsyncSession, account_id: int):
        self.db = db
        self.account_id = account_id

    async def _build_dataset(self, period_days: int = 28) -> list[dict]:
        """Агрегирует статистику Директа + CRM по ключевым словам в компактный датасет."""
        period_end = datetime.utcnow()
        period_start = period_end - timedelta(days=period_days)

        stats_q = await self.db.execute(
            select(
                KeywordStat.keyword_id,
                func.sum(KeywordStat.impressions).label("impressions"),
                func.sum(KeywordStat.clicks).label("clicks"),
                func.sum(KeywordStat.spend).label("spend"),
                func.avg(KeywordStat.avg_position).label("avg_position"),
                func.avg(KeywordStat.avg_bid).label("avg_bid"),
                func.avg(KeywordStat.bounce_rate).label("bounce_rate"),
                func.avg(KeywordStat.traffic_volume).label("traffic_volume"),
            )
            .where(and_(
                KeywordStat.account_id == self.account_id,
                KeywordStat.date >= period_start,
                KeywordStat.date <= period_end,
            ))
            .group_by(KeywordStat.keyword_id)
        )
        stats_by_kw = {row.keyword_id: row for row in stats_q.all()}

        if not stats_by_kw:
            return []

        kw_q = await self.db.execute(
            select(Keyword).where(Keyword.id.in_(list(stats_by_kw.keys())))
        )
        keywords = {kw.id: kw for kw in kw_q.scalars().all()}

        # ── Стратегия кампании: bid_raise/bid_lower применимы через API только
        #    для MANUAL_CPC. Для автостратегий (AUTO/ЕПК) Yandex Direct API
        #    вообще не принимает Keywords[].Bid ("unknown parameter") — ставкой
        #    управляет алгоритм, не рекламодатель напрямую. Прокидываем флаг в
        #    датасет, чтобы модель не предлагала неприменимые изменения ставки.
        ag_ids = list({kw.ad_group_id for kw in keywords.values() if kw.ad_group_id})
        ad_groups = {}
        if ag_ids:
            ag_q = await self.db.execute(select(AdGroup).where(AdGroup.id.in_(ag_ids)))
            ad_groups = {ag.id: ag for ag in ag_q.scalars().all()}
        campaign_ids = list({ag.campaign_id for ag in ad_groups.values() if ag.campaign_id})
        campaigns = {}
        if campaign_ids:
            camp_q = await self.db.execute(select(Campaign).where(Campaign.id.in_(campaign_ids)))
            campaigns = {c.id: c for c in camp_q.scalars().all()}

        def _bid_editable(kw) -> bool:
            ag = ad_groups.get(kw.ad_group_id)
            camp = campaigns.get(ag.campaign_id) if ag else None
            return bool(camp and camp.strategy_type == "MANUAL_CPC")

        # ── CRM: воронка lead → MQL → SQL по ключевому слову ────────────────
        # (заменяет прежнюю агрегацию по deals/revenue — для этого аккаунта
        # интересны не продажи, а количество/стоимость/конверсия в MQL и SQL,
        # см. app/importers/crm_importer.py)
        leads_q = await self.db.execute(
            select(
                Lead.keyword_id,
                func.count(Lead.id).label("leads_count"),
                func.sum(case((Lead.is_mql.is_(True), 1), else_=0)).label("mql_count"),
                func.sum(case((Lead.is_sql.is_(True), 1), else_=0)).label("sql_count"),
            )
            .where(and_(Lead.account_id == self.account_id, Lead.keyword_id.isnot(None)))
            .group_by(Lead.keyword_id)
        )
        leads_by_kw = {}
        for row in leads_q.all():
            leads_by_kw[row.keyword_id] = row

        dataset = []
        for kw_id, s in stats_by_kw.items():
            kw = keywords.get(kw_id)
            if not kw:
                continue
            # v1.7.2: раньше здесь молча выбрасывались все ключи с clicks < 3 —
            # из-за этого в модель уходило 19 строк из сотен, и гипотез было мало.
            # Теперь малокликовые ключи не выбрасываются, а помечаются флагом
            # thin_data: модель видит их, но знает, что решение по ним делать
            # рано — зато может заметить закономерность по группе таких ключей
            # (например, 40 ключей по 1-2 клика съели 30к без единой заявки).
            leads = leads_by_kw.get(kw_id)
            row = {
                "keyword_id": kw_id,
                "phrase": kw.phrase,
                "thin_data": int(s.clicks or 0) < 3,
                "current_bid_rub": float(kw.current_bid) if kw.current_bid else None,
                "bid_editable": _bid_editable(kw),
                "impressions": int(s.impressions or 0),
                "clicks": int(s.clicks or 0),
                "spend_rub": round(float(s.spend or 0), 2),
                "avg_position": round(float(s.avg_position or 0), 2),
                "avg_bid_rub": round(float(s.avg_bid or 0), 2) if s.avg_bid else None,
                "bounce_rate_pct": round(float(s.bounce_rate or 0), 1) if s.bounce_rate else None,
                "traffic_volume": round(float(s.traffic_volume or 0), 1) if s.traffic_volume else None,
            }
            if leads and leads.leads_count:
                leads_count = int(leads.leads_count or 0)
                mql_count = int(leads.mql_count or 0)
                sql_count = int(leads.sql_count or 0)
                row["crm_leads"] = leads_count
                row["crm_mql"] = mql_count
                row["crm_sql"] = sql_count
                row["cr_lead_to_mql_pct"] = round(mql_count / leads_count * 100, 1) if leads_count else None
                row["cr_mql_to_sql_pct"] = round(sql_count / mql_count * 100, 1) if mql_count else None
                row["cpl_rub"] = round(row["spend_rub"] / leads_count, 2) if leads_count else None
                row["cost_per_mql_rub"] = round(row["spend_rub"] / mql_count, 2) if mql_count else None
                row["cost_per_sql_rub"] = round(row["spend_rub"] / sql_count, 2) if sql_count else None
            dataset.append(row)

        dataset.sort(key=lambda r: r["spend_rub"], reverse=True)
        return dataset[: settings.LLM_MAX_KEYWORDS_PER_CALL]

    async def _build_context(self, dataset: list[dict], period_days: int) -> dict:
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
        benchmarks = {
            "median_cost_per_sql_rub": _median([r.get("cost_per_sql_rub") for r in dataset]),
            "median_cost_per_mql_rub": _median([r.get("cost_per_mql_rub") for r in dataset]),
            "median_cpl_rub": _median([r.get("cpl_rub") for r in dataset]),
            "median_bounce_rate_pct": _median([r.get("bounce_rate_pct") for r in dataset]),
            "note": (
                "Сравнивай показатели каждого ключа с этими средними и медианами по "
                "аккаунту — отклонение в разы в любую сторону это и есть повод для гипотезы."
            ),
        }

        # ── Разрез по кампаниям: где сосредоточен бюджет и какая там стратегия
        kw_ids = [r["keyword_id"] for r in dataset]
        camp_by_kw: dict[int, Campaign] = {}
        if kw_ids:
            rows = await self.db.execute(
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

        campaigns = []
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
        period_start = datetime.utcnow() - timedelta(days=period_days)
        sq_rows = await self.db.execute(
            select(
                SearchQuery.query,
                SearchQuery.keyword_phrase,
                func.sum(SearchQuery.clicks).label("clicks"),
                func.sum(SearchQuery.spend).label("spend"),
                func.sum(SearchQuery.impressions).label("impressions"),
            )
            .where(and_(
                SearchQuery.account_id == self.account_id,
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

        return {
            "period_days": period_days,
            "account_totals": account_totals,
            "benchmarks": benchmarks,
            "campaigns": campaigns,
            "long_tail": long_tail,
            "top_search_queries": top_search_queries,
        }

    def _call_llm(self, dataset: list[dict], provider: str, context: dict) -> dict:
        return llm_providers.call_llm(provider, dataset, context)

    def _validate_change(self, change: dict, kw: Optional[Keyword], bid_editable: bool = True) -> Optional[str]:
        """Проверка изменения на безопасные лимиты. Возвращает None если ок,
        иначе текст причины отклонения."""
        if change["change_type"] in ("bid_raise", "bid_lower"):
            if not bid_editable:
                return (
                    "кампания на автоматической стратегии (не MANUAL_CPC) — "
                    "ставка ключевого слова не редактируется через Yandex Direct API"
                )
            if not kw or not kw.current_bid:
                return "нет текущей ставки в БД для сравнения"
            try:
                new_bid = float(str(change.get("recommended_value", "")).replace("₽", "").strip())
            except (ValueError, TypeError):
                return "не удалось распарсить recommended_value как число"
            current = float(kw.current_bid)
            if current > 0:
                pct_change = abs(new_bid - current) / current * 100
                if pct_change > settings.MAX_BID_CHANGE_PCT:
                    return f"изменение ставки {pct_change:.0f}% превышает лимит {settings.MAX_BID_CHANGE_PCT}%"
            if new_bid > settings.MAX_BID_ABSOLUTE_RUB:
                return f"ставка {new_bid}₽ превышает потолок {settings.MAX_BID_ABSOLUTE_RUB}₽"
            if new_bid <= 0:
                return "ставка должна быть положительной"
        return None

    async def generate_suggestions(self, period_days: int = 28, provider: str = "claude") -> list[Suggestion]:
        if provider not in llm_providers.PROVIDERS:
            raise ValueError(f"Неизвестный провайдер '{provider}'. Доступны: {llm_providers.PROVIDERS}")
        if not llm_providers.provider_configured(provider):
            raise RuntimeError(f"API-ключ для провайдера '{provider}' не настроен в .env")

        dataset = await self._build_dataset(period_days)
        if not dataset:
            logger.info(f"LLM analyzer: no data for account {self.account_id}")
            return []

        bid_editable_map = {r["keyword_id"]: r.get("bid_editable", True) for r in dataset}

        analysis = AnalysisResult(
            account_id=self.account_id,
            period_start=datetime.utcnow() - timedelta(days=period_days),
            period_end=datetime.utcnow(),
            summary={"source": "llm", "provider": provider, "keywords_sent": len(dataset), "status": "calling_llm"},
            problems=[],
        )
        self.db.add(analysis)
        await self.db.flush()

        context = await self._build_context(dataset, period_days)

        error_detail = None
        try:
            llm_result = self._call_llm(dataset, provider, context)
        except Exception as e:
            logger.error(f"LLM call failed (provider={provider}): {e}")
            llm_result = {"changes": [], "diagnostics": [], "summary": ""}
            error_detail = str(e)

        changes = llm_result.get("changes", [])
        # v1.7.1 (живой чат с ИИ на вкладке «История вход/выход» вместо голого JSON):
        # diagnostics — пошаговый рассказ модели о ходе анализа человеческим языком,
        # llm_executive_summary — итоговое резюме. Оба поля идут из того же вызова
        # LLM, что и changes — ничего дополнительно не запрашиваем и не платим.
        diagnostics = llm_result.get("diagnostics", [])
        executive_summary = llm_result.get("summary", "")
        # v1.7.3: сколько данных реально доехало до модели после ужимания под
        # лимиты провайдера. Пишем в БД и показываем на фронте — если Groq
        # съел 150 ключей до 15, директолог должен видеть это, а не гадать,
        # почему предложений мало.
        payload_meta = llm_result.get("payload_meta") or {}

        # Сохраняем ЧТО отправили и ЧТО получили — видно на фронте для отладки/доверия к результату.
        analysis.summary = {
            "source": "llm",
            "provider": provider,
            "model": llm_providers.provider_model_name(provider),
            "keywords_sent": len(dataset),
            "llm_input_sample": dataset[:15],   # первые 15 строк датасета, полностью — в БД
            "llm_input_full_count": len(dataset),
            "llm_input_context": context,        # v1.7.2: агрегаты аккаунта/кампаний/спроса
            "llm_payload_meta": payload_meta,    # v1.7.3: что урезано под лимит провайдера
            "llm_raw_output": changes,           # сырой ответ модели, ДО фильтрации safety-лимитами
            "llm_diagnostics": diagnostics,
            "llm_executive_summary": executive_summary,
            "error": error_detail,
        }

        existing_q = await self.db.execute(
            select(Suggestion).where(and_(
                Suggestion.account_id == self.account_id,
                Suggestion.status == SuggestionStatus.pending,
            ))
        )
        existing_keys = {(s.object_id, s.change_type) for s in existing_q.scalars().all()}

        kw_ids = [c.get("keyword_id") for c in changes if c.get("keyword_id")]
        kw_map = {}
        if kw_ids:
            kw_q = await self.db.execute(select(Keyword).where(Keyword.id.in_(kw_ids)))
            kw_map = {k.id: k for k in kw_q.scalars().all()}

        created = []
        rejected_count = 0
        for change in changes:
            kw_id = change.get("keyword_id")
            change_type = change.get("change_type", "check")
            dedup_key = (kw_id or 0, change_type)
            if dedup_key in existing_keys:
                continue

            kw = kw_map.get(kw_id)
            bid_editable = bid_editable_map.get(kw_id, True)
            reject_reason = self._validate_change(change, kw, bid_editable)
            if reject_reason:
                logger.warning(f"LLM change rejected (safety): {change.get('phrase')} — {reject_reason}")
                rejected_count += 1
                continue

            value_after = change.get("recommended_value")
            if change_type == "add_negatives" and change.get("negative_keywords"):
                value_after = ", ".join(change["negative_keywords"])

            s = Suggestion(
                account_id=self.account_id,
                analysis_id=analysis.id,
                object_type="keyword",
                object_id=kw_id or 0,
                object_name=change.get("phrase", ""),
                change_type=change_type,
                value_before=change.get("current_value") or (
                    f"{float(kw.current_bid):.0f}₽" if kw and kw.current_bid else None
                ),
                value_after=value_after,
                rationale=change.get("rationale", "") + f" [источник: LLM-анализ, {provider}]",
                expected_effect=change.get("expected_effect", ""),
                priority=change.get("priority", "this_week"),
                status=SuggestionStatus.pending,
            )
            self.db.add(s)
            created.append(s)
            existing_keys.add(dedup_key)

        await self.db.commit()
        analysis.summary = {
            **(analysis.summary or {}),
            "suggestions_created": len(created),
            "rejected_by_safety_limits": rejected_count,
        }
        await self.db.commit()
        logger.info(
            f"LLM analyzer account={self.account_id}: {len(created)} suggestions created, "
            f"{rejected_count} rejected by safety limits"
        )
        return created
