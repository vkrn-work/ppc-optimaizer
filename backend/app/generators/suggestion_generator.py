"""
Генератор предложений по изменениям ставок и стратегий.
Применяет правила из таблицы rules (хранятся в БД).
Приоритеты: today / this_week / month / scale
"""
from decimal import Decimal
from typing import Optional
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    Keyword, KeywordMetrics, AnalysisResult, Rule, Suggestion, Account, Cluster
)
from app.core.config import settings

logger = logging.getLogger(__name__)

# Типы изменений
BID_RAISE = "bid_raise"
BID_LOWER = "bid_lower"
BID_HOLD = "bid_hold"
STRATEGY_CPA = "strategy_cpa"
ADD_NEGATIVES = "add_negatives"
DISABLE_KEYWORD = "disable_keyword"
EXPAND_SEMANTICS = "expand_semantics"


class SuggestionGenerator:
    def __init__(self, db: AsyncSession, account_id: int):
        self.db = db
        self.account_id = account_id

    async def generate_for_analysis(self, analysis: AnalysisResult) -> list[Suggestion]:
        """Сгенерировать все предложения для результата анализа"""
        suggestions = []

        # Загрузить правила (сначала аккаунт-специфичные, потом глобальные)
        rules = await self._load_rules()

        # Загрузить метрики ключей для этого анализа
        from sqlalchemy import and_
        metrics_result = await self.db.execute(
            select(KeywordMetrics).where(
                and_(
                    KeywordMetrics.analysis_id == analysis.id,
                    KeywordMetrics.account_id == self.account_id,
                )
            )
        )
        keyword_metrics = metrics_result.scalars().all()

        account_result = await self.db.execute(
            select(Account).where(Account.id == self.account_id)
        )
        account = account_result.scalar_one_or_none()

        for km in keyword_metrics:
            kw_result = await self.db.execute(
                select(Keyword).where(Keyword.id == km.keyword_id)
            )
            keyword = kw_result.scalar_one_or_none()
            if not keyword:
                continue

            suggestion = await self._evaluate_keyword(km, keyword, account, rules, analysis.id)
            if suggestion:
                self.db.add(suggestion)
                suggestions.append(suggestion)

        await self.db.flush()
        logger.info(f"Generated {len(suggestions)} suggestions for analysis {analysis.id}")
        return suggestions

    async def _load_rules(self) -> list[Rule]:
        """Загрузить правила — сначала для аккаунта, потом глобальные"""
        from sqlalchemy import or_, and_
        result = await self.db.execute(
            select(Rule).where(
                and_(
                    Rule.is_active == True,
                    or_(Rule.account_id == self.account_id, Rule.account_id == None),
                )
            ).order_by(Rule.account_id.desc().nullslast())
        )
        return result.scalars().all()

    async def _evaluate_keyword(
        self,
        km: KeywordMetrics,
        keyword: Keyword,
        account: Optional[Account],
        rules: list[Rule],
        analysis_id: int,
    ) -> Optional[Suggestion]:
        """Применить правила к ключу и сгенерировать предложение"""
        cr = float(km.cr_click_lead or 0)
        clicks = km.clicks
        cpql = float(km.cpql or 0) if km.cpql else None
        target_cpql = float(account.target_cpql or 0) if account and account.target_cpql else None
        current_bid = float(keyword.current_bid or 0)
        recommended_bid = float(km.recommended_bid or 0) if km.recommended_bid else None

        # ── Правило 1: Высококонверсионный ключ — поднять ставку ──────────────
        if (cr > settings.CR_HIGH_THRESHOLD and clicks >= settings.MIN_CLICKS_KEYWORD and recommended_bid):
            if recommended_bid > current_bid * 1.05:  # повышение > 5%
                return Suggestion(
                    account_id=self.account_id,
                    analysis_id=analysis_id,
                    object_type="keyword",
                    object_id=keyword.id,
                    object_name=keyword.phrase,
                    change_type=BID_RAISE,
                    value_before=f"{current_bid:.0f}₽",
                    value_after=f"{recommended_bid:.0f}₽",
                    rationale=(
                        f"CR {cr*100:.1f}% > 15% при {clicks} кликах. "
                        f"Высокая конверсионность — нужно больше трафика. "
                        f"Ставка рассчитана по формуле CPL_цель × CR."
                    ),
                    expected_effect=f"Рост кликов на 20–40%. При сохранении CR: +{int(clicks*0.3 * cr):.0f} лидов/мес.",
                    priority="today",
                )

        # ── Правило 2: Нормальный CR — держать или незначительно скорректировать ──
        if (settings.CR_MID_THRESHOLD <= cr <= settings.CR_HIGH_THRESHOLD
                and clicks >= settings.MIN_CLICKS_KEYWORD):
            if recommended_bid and abs(recommended_bid - current_bid) / max(current_bid, 1) > 0.15:
                direction = BID_RAISE if recommended_bid > current_bid else BID_LOWER
                return Suggestion(
                    account_id=self.account_id,
                    analysis_id=analysis_id,
                    object_type="keyword",
                    object_id=keyword.id,
                    object_name=keyword.phrase,
                    change_type=direction,
                    value_before=f"{current_bid:.0f}₽",
                    value_after=f"{recommended_bid:.0f}₽",
                    rationale=f"CR {cr*100:.1f}% — хорошая конверсионность. Корректировка ставки для оптимального CPL.",
                    expected_effect="Стабилизация позиций и CPL.",
                    priority="this_week",
                )

        # ── Правило 3: Низкий CR при достаточных данных → CPA стратегия ──────
        if (settings.CR_CRITICAL_THRESHOLD < cr <= settings.CR_LOW_THRESHOLD
                and clicks >= settings.MIN_CLICKS_CAMPAIGN):
            return Suggestion(
                account_id=self.account_id,
                analysis_id=analysis_id,
                object_type="keyword",
                object_id=keyword.id,
                object_name=keyword.phrase,
                change_type=STRATEGY_CPA,
                value_before="CPC",
                value_after="CPA",
                rationale=(
                    f"CR {cr*100:.1f}% при {clicks} кликах — много кликов, мало заявок. "
                    f"Расход: {float(km.spend):.0f}₽, лидов: {km.leads}. "
                    f"CPA-стратегия оптимизирует ставку автоматически."
                ),
                expected_effect="Снижение расхода без конверсий. Алгоритм обучится на имеющихся данных.",
                priority="this_week",
            )

        # ── Правило 4: Очень низкий CR — информационный трафик → минус/отключить ──
        if cr < settings.CR_CRITICAL_THRESHOLD and clicks >= settings.MIN_CLICKS_CAMPAIGN:
            return Suggestion(
                account_id=self.account_id,
                analysis_id=analysis_id,
                object_type="keyword",
                object_id=keyword.id,
                object_name=keyword.phrase,
                change_type=ADD_NEGATIVES,
                value_before="Активен",
                value_after="Минус-слова / отключить",
                rationale=(
                    f"CR {cr*100:.2f}% < 1% при {clicks} кликах. "
                    f"Расход {float(km.spend):.0f}₽ без результата. "
                    f"Вероятен информационный запрос без коммерческого интента."
                ),
                expected_effect=f"Экономия ~{float(km.spend):.0f}₽/мес. Перераспределить бюджет на конверсионные ключи.",
                priority="today",
            )

        # ── Правило 5: CPQL сильно выше цели — снизить ставку ────────────────
        if (cpql and target_cpql and cpql > target_cpql * 1.5
                and clicks >= settings.MIN_CLICKS_KEYWORD):
            new_bid = current_bid * 0.8
            return Suggestion(
                account_id=self.account_id,
                analysis_id=analysis_id,
                object_type="keyword",
                object_id=keyword.id,
                object_name=keyword.phrase,
                change_type=BID_LOWER,
                value_before=f"{current_bid:.0f}₽",
                value_after=f"{new_bid:.0f}₽",
                rationale=(
                    f"CPQL {cpql:.0f}₽ превышает цель {target_cpql:.0f}₽ в {cpql/target_cpql:.1f}×. "
                    f"Снижение ставки на 20%."
                ),
                expected_effect=f"Снижение CPQL до ~{cpql*0.8:.0f}₽. Возможное незначительное падение трафика.",
                priority="this_week",
            )

        return None

    async def generate_scale_suggestions(self, analysis: AnalysisResult) -> list[Suggestion]:
        """Предложения по расширению семантики на основе конверсионных кластеров"""
        suggestions = []

        # Найти кластеры с высоким CR для масштабирования
        clusters_result = await self.db.execute(
            select(Cluster).where(Cluster.account_id == self.account_id)
        )
        clusters = clusters_result.scalars().all()

        for opportunity in (analysis.opportunities or []):
            if opportunity.get("type") == "high_cr_keyword":
                s = Suggestion(
                    account_id=self.account_id,
                    analysis_id=analysis.id,
                    object_type="keyword",
                    object_id=opportunity["keyword_id"],
                    object_name=opportunity["phrase"],
                    change_type=EXPAND_SEMANTICS,
                    value_before="Текущая семантика",
                    value_after="Расширить кластер",
                    rationale=(
                        f"CR {opportunity['cr']}% подтверждён на {opportunity['clicks']} кликах. "
                        f"Расширение семантики по этому направлению даст дополнительный трафик."
                    ),
                    expected_effect="+5–10 кликов/нед по направлению при добавлении 3–5 ключей.",
                    priority="scale",
                )
                self.db.add(s)
                suggestions.append(s)

        await self.db.flush()
        return suggestions
