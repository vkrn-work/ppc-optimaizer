"""
LLM-анализатор. В отличие от cr_analyzer.py (жёсткие пороги), здесь LLM
получает агрегированные данные по ключевым словам (Директ + CRM) и сам решает,
какие проблемы есть и что с ними делать.

Пайплайн:
  1. _build_dataset()      — джойн keyword_stats + leads по keyword_id/utm_term,
                              агрегация в компактную таблицу (не сырые построчные данные).
  2. llm_providers.call_llm() — вызов выбранного провайдера (Claude/Gemini/Groq/OpenRouter),
                              модель обязана вернуть строго типизированный список изменений.
  3. generate_suggestions() — валидация ответа модели (лимиты из config.py) и запись
                              в таблицу suggestions (общая с cr_analyzer, status=pending).

Так же, как для cr_analyzer, safety-валидация лимитов ставки происходит на этапе
записи — модель не может напрямую менять кабинет.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, and_, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.analyzers import llm_providers
from app.models.models import (
    Keyword, KeywordStat, AdGroup, Campaign, Lead, LeadStatus,
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

        leads_q = await self.db.execute(
            select(
                Lead.keyword_id,
                func.count(Lead.id).label("leads_count"),
                func.sum(case((Lead.status == LeadStatus.deal, 1), else_=0)).label("deals"),
                func.sum(Lead.revenue).label("revenue"),
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
            if not kw or not s.clicks or s.clicks < 3:
                continue  # слишком мало данных, чтобы предлагать LLM решать
            leads = leads_by_kw.get(kw_id)
            row = {
                "keyword_id": kw_id,
                "phrase": kw.phrase,
                "current_bid_rub": float(kw.current_bid) if kw.current_bid else None,
                "impressions": int(s.impressions or 0),
                "clicks": int(s.clicks or 0),
                "spend_rub": round(float(s.spend or 0), 2),
                "avg_position": round(float(s.avg_position or 0), 2),
                "avg_bid_rub": round(float(s.avg_bid or 0), 2) if s.avg_bid else None,
                "bounce_rate_pct": round(float(s.bounce_rate or 0), 1) if s.bounce_rate else None,
                "traffic_volume": round(float(s.traffic_volume or 0), 1) if s.traffic_volume else None,
            }
            if leads:
                row["crm_leads"] = int(leads.leads_count or 0)
                row["crm_deals"] = int(leads.deals or 0)
                row["crm_revenue_rub"] = round(float(leads.revenue or 0), 2) if leads.revenue else None
                if leads.leads_count:
                    row["cpl_rub"] = round(row["spend_rub"] / leads.leads_count, 2)
            dataset.append(row)

        dataset.sort(key=lambda r: r["spend_rub"], reverse=True)
        return dataset[: settings.LLM_MAX_KEYWORDS_PER_CALL]

    def _call_llm(self, dataset: list[dict], provider: str) -> list[dict]:
        return llm_providers.call_llm(provider, dataset)

    def _validate_change(self, change: dict, kw: Optional[Keyword]) -> Optional[str]:
        """Проверка изменения на безопасные лимиты. Возвращает None если ок,
        иначе текст причины отклонения."""
        if change["change_type"] in ("bid_raise", "bid_lower"):
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

        analysis = AnalysisResult(
            account_id=self.account_id,
            period_start=datetime.utcnow() - timedelta(days=period_days),
            period_end=datetime.utcnow(),
            summary={"source": "llm", "provider": provider, "keywords_sent": len(dataset), "status": "calling_llm"},
            problems=[],
        )
        self.db.add(analysis)
        await self.db.flush()

        error_detail = None
        try:
            changes = self._call_llm(dataset, provider)
        except Exception as e:
            logger.error(f"LLM call failed (provider={provider}): {e}")
            changes = []
            error_detail = str(e)

        # Сохраняем ЧТО отправили и ЧТО получили — видно на фронте для отладки/доверия к результату.
        analysis.summary = {
            "source": "llm",
            "provider": provider,
            "model": llm_providers.provider_model_name(provider),
            "keywords_sent": len(dataset),
            "llm_input_sample": dataset[:15],   # первые 15 строк датасета, полностью — в БД
            "llm_input_full_count": len(dataset),
            "llm_raw_output": changes,           # сырой ответ модели, ДО фильтрации safety-лимитами
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
            reject_reason = self._validate_change(change, kw)
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
