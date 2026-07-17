"""
Генератор предложений (Suggestions) для аппрува директологом.

v2.0 (CHANGED): приведён в соответствие с cr_analyzer v4.1.
Анализатор выдаёт сигналы с type ∈ {5A,5B,4A,4B,8A,9A,1E,P1}.
Каждый сигнал из analysis.problems → строка в таблице suggestions (status=pending).
Дедупликация по (object_id, change_type) среди pending.

Контур аппрува: фронт вызывает POST /suggestions/{id}/action →
suggestion.status меняется, создаётся Hypothesis со ссылкой suggestion_id.
"""
import logging
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    Keyword, AnalysisResult, Suggestion, SuggestionStatus,
)

logger = logging.getLogger(__name__)

# Маппинг реального типа сигнала анализатора v4.1 → тип изменения.
SIGNAL_TO_CHANGE_TYPE = {
    "5A": "bid_raise",       # ставка не держит аукцион → поднять
    "5B": "bid_raise",       # конкуренты перебили / сезон → поднять либо держать
    "4A": "ad_rewrite",      # креатив проигрывает → переписать заголовок
    "4B": "ad_test",         # выгорание креатива → A/B
    "8A": "add_negatives",   # мусорный трафик / нецелевой лендинг → минус-слова
    "9A": "bid_lower",       # переплата на потолке трафика → снизить
    "1E": "site_check",      # разрыв клики/визиты → проверить сайт, пауза
    "P1": "bid_raise",       # недоинвестированный конверсионник → поднять
}

# Сигналы, по которым имеет смысл конкретная рекомендованная ставка.
BID_SIGNALS = {"5A", "5B", "9A", "P1"}


class SuggestionGenerator:
    def __init__(self, db: AsyncSession, account_id: int):
        self.db = db
        self.account_id = account_id

    async def generate_for_analysis(self, analysis: AnalysisResult) -> list[Suggestion]:
        if not analysis.problems:
            return []

        existing_q = await self.db.execute(
            select(Suggestion).where(
                and_(
                    Suggestion.account_id == self.account_id,
                    Suggestion.status == SuggestionStatus.pending,
                )
            )
        )
        existing = existing_q.scalars().all()
        existing_keys = {(s.object_id, s.change_type) for s in existing}

        suggestions = []
        for problem in analysis.problems:
            sig_type = problem.get("type", "")
            severity = problem.get("severity", "warning")
            kw_id = problem.get("keyword_id")
            phrase = problem.get("phrase") or ""
            action = problem.get("action", "")
            description = problem.get("description", "")
            hypothesis = problem.get("hypothesis", "")
            rec_bid = problem.get("recommended_bid")

            change_type = SIGNAL_TO_CHANGE_TYPE.get(sig_type, "check")
            entity_id = kw_id or 0
            entity_type = "keyword"

            # приоритет по severity
            priority = {"critical": "today", "warning": "this_week", "info": "month"}.get(
                severity, "this_week"
            )

            value_before = None
            value_after = None
            if kw_id and sig_type in BID_SIGNALS:
                kw_res = await self.db.execute(
                    select(Keyword).where(Keyword.id == kw_id)
                )
                kw = kw_res.scalar_one_or_none()
                if kw and kw.current_bid:
                    value_before = f"{float(kw.current_bid):.0f}₽"
                if rec_bid:
                    value_after = f"{float(rec_bid):.0f}₽"
            elif sig_type in ("4A", "4B"):
                value_before = "Текущее объявление"
                value_after = "Новый вариант заголовка"
            elif sig_type == "8A":
                value_before = "Без минус-слов"
                value_after = "Добавить минус-слова по мусорным запросам"
            elif sig_type == "1E":
                value_before = "Ставки активны"
                value_after = "Пауза ставок до проверки сайта"

            dedup_key = (entity_id, change_type)
            if dedup_key in existing_keys:
                continue

            rationale = " | ".join(filter(None, [
                description,
                f"Гипотеза: {hypothesis}" if hypothesis else "",
            ]))

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
                expected_effect=action,
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

    async def generate_scale_suggestions(self, analysis: AnalysisResult) -> list[Suggestion]:
        """Точки роста из analysis.opportunities (если анализатор их заполняет)."""
        if not analysis.opportunities:
            return []
        suggestions = []
        for opp in analysis.opportunities:
            kw_id = opp.get("keyword_id")
            if not kw_id:
                continue
            phrase = opp.get("phrase") or ""
            rec_bid = opp.get("recommended_bid")
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
            kw_res = await self.db.execute(select(Keyword).where(Keyword.id == kw_id))
            kw = kw_res.scalar_one_or_none()
            if not kw:
                continue
            s = Suggestion(
                account_id=self.account_id,
                analysis_id=analysis.id,
                object_type="keyword",
                object_id=kw_id,
                object_name=phrase,
                change_type="bid_raise",
                value_before=f"{float(kw.current_bid):.0f}₽" if kw.current_bid else None,
                value_after=f"{float(rec_bid):.0f}₽" if rec_bid else None,
                rationale=f"Точка роста. {opp.get('action', '')}",
                expected_effect=opp.get("expected_outcome", ""),
                priority="scale",
                status=SuggestionStatus.pending,
            )
            self.db.add(s)
            suggestions.append(s)
        await self.db.flush()
        logger.info(f"Generated {len(suggestions)} scale suggestions for analysis {analysis.id}")
        return suggestions
