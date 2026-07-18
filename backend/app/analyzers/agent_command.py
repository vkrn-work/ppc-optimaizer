"""
Агент-исполнитель свободных текстовых команд для страницы «Задачи ИИ»
(например: «добавь в рекламу трубу стальную электросварную 89х3.5»).

LLM получает список уже существующих групп объявлений в кабинете (id,
название группы, название кампании, число ключей) и генерирует:
  - список новых релевантных ключевых фраз под описанный товар,
  - id наиболее подходящей СУЩЕСТВУЮЩЕЙ группы объявлений (не выдумывает id —
    выбирает только из переданного списка),
  - минус-слова для этой же группы, чтобы сразу отсечь мусорный трафик.

Ничего не пишет в Директ напрямую. Создаёт pending Suggestion(s)
(change_type=add_keywords [+ add_negatives]), которые проходят через тот же
approve/apply-пайплайн, что и обычные предложения ИИ-анализа — см.
app.core.tasks._apply_suggestion_async. Это осознанное решение по безопасности:
на живом кабинете с реальным бюджетом изменения применяются только после
ручного одобрения человеком, бот не имеет права применить их сам.
"""
import logging
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.analyzers import llm_providers
from app.models.models import (
    AdGroup, Campaign, Keyword, AnalysisResult, Suggestion, SuggestionStatus,
)

logger = logging.getLogger(__name__)

MAX_AD_GROUPS_IN_CONTEXT = 300
MAX_KEYWORDS_PER_COMMAND = 40


class CommandAgentError(Exception):
    pass


class CommandAgent:
    def __init__(self, db: AsyncSession, account_id: int):
        self.db = db
        self.account_id = account_id

    async def _build_context(self) -> list[dict]:
        """Существующие группы объявлений — контекст, чтобы модель выбирала
        ТОЛЬКО из реально существующих групп этого кабинета, а не выдумывала id."""
        kw_count_q = await self.db.execute(
            select(Keyword.ad_group_id, func.count(Keyword.id).label("cnt"))
            .where(Keyword.account_id == self.account_id)
            .group_by(Keyword.ad_group_id)
        )
        kw_counts = {r.ad_group_id: r.cnt for r in kw_count_q}

        q = await self.db.execute(
            select(AdGroup, Campaign)
            .join(Campaign, Campaign.id == AdGroup.campaign_id)
            .where(AdGroup.account_id == self.account_id, Campaign.is_active == True)
            .order_by(Campaign.name, AdGroup.name)
        )
        rows = q.all()
        return [{
            "ad_group_id": ag.id,
            "ad_group_name": ag.name,
            "campaign_name": camp.name,
            "existing_keywords_count": kw_counts.get(ag.id, 0),
        } for ag, camp in rows[:MAX_AD_GROUPS_IN_CONTEXT]]

    async def run_command(self, command_text: str, provider: str) -> dict:
        context = await self._build_context()
        if not context:
            raise CommandAgentError(
                "В кабинете нет собранных групп объявлений — сначала запустите сбор данных "
                "(кнопка «Обновить данные»)."
            )

        analysis = AnalysisResult(
            account_id=self.account_id,
            period_start=datetime.utcnow(),
            period_end=datetime.utcnow(),
            summary={
                "source": "agent_command",
                "provider": provider,
                "command": command_text,
                "status": "calling_llm",
            },
            problems=[],
        )
        self.db.add(analysis)
        await self.db.flush()

        try:
            plan = llm_providers.call_command_agent(provider, command_text, context)
        except Exception as e:
            logger.error(f"agent-command LLM call failed (provider={provider}): {e}")
            analysis.summary = {**analysis.summary, "status": "llm_error", "error": str(e)}
            await self.db.commit()
            raise CommandAgentError(f"Ошибка обращения к {provider}: {e}")

        keywords = [k.strip() for k in (plan.get("keywords") or []) if k and str(k).strip()][:MAX_KEYWORDS_PER_COMMAND]
        negatives = [n.strip() for n in (plan.get("negative_keywords") or []) if n and str(n).strip()]
        target_id = plan.get("target_ad_group_id")
        rationale = plan.get("rationale", "")

        if not keywords:
            analysis.summary = {**analysis.summary, "status": "no_keywords", "llm_raw": plan}
            await self.db.commit()
            raise CommandAgentError("ИИ не смог сгенерировать ключевые фразы по этой команде — уточните запрос.")

        ag_row = next((r for r in context if r["ad_group_id"] == target_id), None)
        if not ag_row:
            analysis.summary = {
                **analysis.summary,
                "status": "no_target_group",
                "keywords": keywords,
                "negative_keywords": negatives,
                "suggested_ad_group_name": plan.get("suggested_ad_group_name"),
                "rationale": rationale,
                "llm_raw": plan,
            }
            await self.db.commit()
            suggested_name = plan.get("suggested_ad_group_name") or ""
            return {
                "status": "no_target",
                "keywords": keywords,
                "negative_keywords": negatives,
                "suggested_ad_group_name": suggested_name,
                "rationale": rationale,
                "message": (
                    "Не нашёл подходящую существующую группу объявлений для этой команды. "
                    + (f"Предлагаемое название новой группы: «{suggested_name}». " if suggested_name else "")
                    + "Создайте группу вручную в Директе и повторите команду, либо уточните, "
                      "в какую из существующих групп добавить эти ключи."
                ),
            }

        suggestion_ids = []
        kw_suggestion = Suggestion(
            account_id=self.account_id,
            analysis_id=analysis.id,
            object_type="ad_group",
            object_id=ag_row["ad_group_id"],
            object_name=f"{ag_row['campaign_name']} → {ag_row['ad_group_name']}",
            change_type="add_keywords",
            value_before=f"{ag_row['existing_keywords_count']} ключей в группе",
            value_after=", ".join(keywords),
            rationale=f"Команда: «{command_text}». {rationale} [источник: задача ИИ-агенту, {provider}]",
            expected_effect=f"Добавление {len(keywords)} новых ключевых фраз по запросу пользователя",
            priority="today",
            status=SuggestionStatus.pending,
        )
        self.db.add(kw_suggestion)
        await self.db.flush()
        suggestion_ids.append(kw_suggestion.id)

        if negatives:
            neg_suggestion = Suggestion(
                account_id=self.account_id,
                analysis_id=analysis.id,
                object_type="ad_group",
                object_id=ag_row["ad_group_id"],
                object_name=f"{ag_row['campaign_name']} → {ag_row['ad_group_name']}",
                change_type="add_negatives",
                value_before=None,
                value_after=", ".join(negatives),
                rationale=f"Минус-слова для новых ключей по команде «{command_text}» [источник: задача ИИ-агенту, {provider}]",
                expected_effect="Отсечь нерелевантный трафик по новым ключам",
                priority="today",
                status=SuggestionStatus.pending,
            )
            self.db.add(neg_suggestion)
            await self.db.flush()
            suggestion_ids.append(neg_suggestion.id)

        analysis.summary = {
            **analysis.summary,
            "status": "created",
            "target_ad_group_name": f"{ag_row['campaign_name']} → {ag_row['ad_group_name']}",
            "keywords": keywords,
            "negative_keywords": negatives,
            "rationale": rationale,
            "suggestion_ids": suggestion_ids,
        }
        await self.db.commit()

        logger.info(
            f"agent_command account={self.account_id}: {len(keywords)} keywords → "
            f"ad_group {ag_row['ad_group_id']}, suggestions={suggestion_ids}"
        )
        return {
            "status": "created",
            "target": f"{ag_row['campaign_name']} → {ag_row['ad_group_name']}",
            "keywords": keywords,
            "negative_keywords": negatives,
            "rationale": rationale,
            "suggestion_ids": suggestion_ids,
            "message": (
                f"Готово: {len(keywords)} ключевых фраз"
                + (f" и {len(negatives)} минус-слов" if negatives else "")
                + f" подготовлены для группы «{ag_row['ad_group_name']}». "
                  "Проверьте и одобрите на странице «ИИ-анализ» → «Предложения»."
            ),
        }
