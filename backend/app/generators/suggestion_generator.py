"""
Генератор предложений (Suggestions) для аппрува директологом.

v1.2: работает на основе сигналов из AnalysisResult.problems,
не требует CRM-данных (KeywordMetrics). CRM-логика будет
активирована при подключении Level 2.

Каждый сигнал из cr_analyzer → Suggestion со статусом pending.
Директолог аппрувает или отклоняет → создаётся Hypothesis.
"""
from decimal import Decimal
from typing import Optional
import logging
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    Keyword, AnalysisResult, Suggestion, SuggestionStatus,
    Account, Campaign, AdGroup
)

logger = logging.getLogger(__name__)

# Маппинг типа сигнала → тип изменения
SIGNAL_TO_CHANGE_TYPE = {
    "low_position":         "bid_raise",
    "traffic_drop":         "bid_raise",
    "zero_ctr":             "ad_check",
    "low_ctr":              "ad_check",
    "click_position_gap":   "bid_check",
    "spend_no_conversion":  "bid_lower",
    "epk_bid_collapse":     "strategy_review",
    "cpc_spike":            "bid_check",
    "high_bounce_rate":     "landing_fix",
    "low_page_depth":       "landing_fix",
    "low_visit_duration":   "landing_fix",
    "mobile_quality_issue": "bid_adjust",
    "scale_opportunity":    "bid_raise",
}

# Типы сигналов которые касаются кампании, не конкретного ключа
CAMPAIGN_LEVEL_SIGNALS = {"epk_bid_collapse", "high_bounce_rate",
                          "low_page_depth", "low_visit_duration",
                          "mobile_quality_issue"}


class SuggestionGenerator:
    def __init__(self, db: AsyncSession, account_id: int):
        self.db         = db
        self.account_id = account_id

    async def generate_for_analysis(self, analysis: AnalysisResult) -> list[Suggestion]:
        """
        Конвертирует сигналы из analysis.problems в Suggestion записи.
        Дубликаты не создаются: если для этого же keyword_id и типа
        уже есть pending-предложение из предыдущего анализа — пропускаем.
        """
        if not analysis.problems:
            return []

        # Загрузить уже существующие pending-предложения для этого кабинета
        existing_q = await self.db.execute(
            select(Suggestion).where(
                and_(
                    Suggestion.account_id == self.account_id,
                    Suggestion.status == SuggestionStatus.pending,
                )
            )
        )
        existing = existing_q.scalars().all()
        existing_keys = {
            (s.object_id, s.change_type) for s in existing
        }

        suggestions = []
        for problem in analysis.problems:
            sig_type   = problem.get("type", "")
            severity   = problem.get("severity", "warning")
            priority   = problem.get("priority", "this_week")
            kw_id      = problem.get("keyword_id")
            phrase     = problem.get("phrase") or problem.get("entity_name") or ""
            action     = problem.get("action", "")
            description = problem.get("description", "")
            hypothesis  = problem.get("hypothesis", "")
            expected    = problem.get("expected_outcome", "")
            calc_logic  = problem.get("calculation_logic", "")
            rec_bid    = problem.get("recommended_bid")
            entity_id  = problem.get("entity_id") or kw_id or 0
            entity_type = "campaign" if sig_type in CAMPAIGN_LEVEL_SIGNALS else "keyword"

            change_type = SIGNAL_TO_CHANGE_TYPE.get(sig_type, "check")

            # Для ключевых предложений со ставкой — нужен актуальный current_bid
            value_before = None
            value_after  = None

            if kw_id and sig_type in ("low_position", "traffic_drop",
                                       "spend_no_conversion", "scale_opportunity"):
                kw_res = await self.db.execute(
                    select(Keyword).where(Keyword.id == kw_id)
                )
                kw = kw_res.scalar_one_or_none()
                if kw and kw.current_bid:
                    value_before = f"{float(kw.current_bid):.0f}₽"
                if rec_bid:
                    value_after = f"{rec_bid:.0f}₽"

            elif sig_type == "epk_bid_collapse":
                value_before = "ЕПК (автоснижение ставок)"
                value_after  = "Ручное восстановление / перевод на ТГК"

            elif sig_type in ("zero_ctr", "low_ctr"):
                value_before = "Текущее объявление"
                value_after  = "Новый вариант с УТП"

            elif sig_type in ("high_bounce_rate", "low_page_depth",
                               "low_visit_duration"):
                value_before = "Текущая посадочная"
                value_after  = "Оптимизация посадочной"

            elif sig_type == "mobile_quality_issue":
                value_before = "Нет корректировки на mobile"
                value_after  = "Корректировка ставок -50% на mobile"

            # Составить ключ для дедупликации
            dedup_key = (entity_id, change_type)
            if dedup_key in existing_keys:
                continue

            # Собрать полное обоснование
            rationale_parts = [description]
            if hypothesis:
                rationale_parts.append(f"Гипотеза: {hypothesis}")
            if calc_logic:
                rationale_parts.append(f"Расчёт: {calc_logic}")
            rationale = " | ".join(filter(None, rationale_parts))

            s = Suggestion(
                account_id=self.account_id,
                analysis_id=analysis.id,
                object_type=entity_type,
                object_id=entity_id,
                object_name=phrase,
                change_type=change_type,
                value_before=value_before,
                value_after=value_after,
                rationale=rationale,
                expected_effect=expected or action,
                priority=priority,
                status=SuggestionStatus.pending,
            )
            self.db.add(s)
            suggestions.append(s)
            existing_keys.add(dedup_key)

        await self.db.flush()
        logger.info(
            f"Generated {len(suggestions)} suggestions for analysis {analysis.id}"
            f" (account {self.account_id})"
        )
        return suggestions

    async def generate_scale_suggestions(
        self, analysis: AnalysisResult
    ) -> list[Suggestion]:
        """
        Предложения масштабирования из analysis.opportunities.
        Создаёт записи для точек роста (S-050 scale_opportunity).
        """
        if not analysis.opportunities:
            return []

        suggestions = []
        for opp in analysis.opportunities:
            kw_id   = opp.get("keyword_id")
            phrase  = opp.get("phrase") or ""
            rec_bid = opp.get("recommended_bid")
            action  = opp.get("action", "")
            expected = opp.get("expected_outcome", "")

            if not kw_id:
                continue

            # Проверяем нет ли уже такого
            existing_q = await self.db.execute(
                select(Suggestion).where(
                    and_(
                        Suggestion.account_id == self.account_id,
                        Suggestion.object_id == kw_id,
                        Suggestion.change_type == "bid_raise",
                        Suggestion.status == SuggestionStatus.pending,
                    )
                )
            )
            if existing_q.scalar_one_or_none():
                continue

            kw_res = await self.db.execute(
                select(Keyword).where(Keyword.id == kw_id)
            )
            kw = kw_res.scalar_one_or_none()
            if not kw:
                continue

            value_before = f"{float(kw.current_bid):.0f}₽" if kw.current_bid else None
            value_after  = f"{rec_bid:.0f}₽" if rec_bid else None

            s = Suggestion(
                account_id=self.account_id,
                analysis_id=analysis.id,
                object_type="keyword",
                object_id=kw_id,
                object_name=phrase,
                change_type="bid_raise",
                value_before=value_before,
                value_after=value_after,
                rationale=(
                    f"Точка роста: CTR {opp.get('metric_value', 0):.1f}%"
                    f" при {opp.get('clicks', 0)} кликах. {action}"
                ),
                expected_effect=expected,
                priority="scale",
                status=SuggestionStatus.pending,
            )
            self.db.add(s)
            suggestions.append(s)

        await self.db.flush()
        logger.info(
            f"Generated {len(suggestions)} scale suggestions for analysis {analysis.id}"
        )
        return suggestions
