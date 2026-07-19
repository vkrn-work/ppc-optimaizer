"""
Планировщик новых рекламных кампаний (v1.7.0, пункт 6 запроса пользователя).

Свободная команда («создай кампанию по маркам 1.4310 и S315MC», «запусти
направление по трубам профильным») → LLM проектирует кампанию целиком:
название, бюджет, группы, ключи, минус-слова, объявления — и сохраняет
черновик как pending Suggestion(change_type="create_campaign", payload=...).

Как и app.analyzers.agent_command.CommandAgent, этот модуль НИЧЕГО не пишет
в Директ напрямую — только создаёт pending-предложение. Применение (реальные
campaigns.add/adgroups.add/keywords.add/ads.add) происходит через тот же
approve/apply-контур, что и все остальные типы предложений, см.
app.core.tasks._apply_suggestion_async (ветка change_type == "create_campaign")
и app.collectors.direct_writer.YandexDirectWriter.create_full_campaign.

Контекст для модели: чтобы предложение не было "из воздуха", в market_context
передаются кластеры/ключи с лучшим CR из уже собранных данных аккаунта (если
они есть) — например, если пользователь просит "кампанию по маркам с лучшей
конверсией", модель видит реальные цифры, а не гадает.
"""
import logging
from datetime import datetime

from sqlalchemy import select, func, and_, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.analyzers import llm_providers
from app.models.models import (
    Keyword, KeywordStat, AdGroup, Campaign, Lead,
    AnalysisResult, Suggestion, SuggestionStatus,
)

logger = logging.getLogger(__name__)

MAX_CONTEXT_KEYWORDS = 40


class CampaignPlannerError(Exception):
    pass


class CampaignPlanner:
    def __init__(self, db: AsyncSession, account_id: int):
        self.db = db
        self.account_id = account_id

    async def _build_market_context(self) -> list[dict]:
        """Ключи с лучшим CR/наибольшим объёмом — ориентир для модели о том,
        что уже хорошо работает в этом кабинете (не обязателен, может быть
        пустым для полностью нового направления)."""
        stats_q = await self.db.execute(
            select(
                KeywordStat.keyword_id,
                func.sum(KeywordStat.clicks).label("clicks"),
                func.sum(KeywordStat.spend).label("spend"),
            )
            .where(KeywordStat.account_id == self.account_id)
            .group_by(KeywordStat.keyword_id)
            .having(func.sum(KeywordStat.clicks) >= 3)
            .order_by(func.sum(KeywordStat.spend).desc())
            .limit(MAX_CONTEXT_KEYWORDS)
        )
        rows = stats_q.all()
        if not rows:
            return []
        kw_ids = [r.keyword_id for r in rows]
        kw_q = await self.db.execute(select(Keyword).where(Keyword.id.in_(kw_ids)))
        kw_map = {k.id: k for k in kw_q.scalars().all()}

        leads_q = await self.db.execute(
            select(
                Lead.keyword_id,
                func.count(Lead.id).label("leads"),
                func.sum(case((Lead.is_sql.is_(True), 1), else_=0)).label("sql"),
            )
            .where(and_(Lead.account_id == self.account_id, Lead.keyword_id.in_(kw_ids)))
            .group_by(Lead.keyword_id)
        )
        leads_map = {r.keyword_id: r for r in leads_q.all()}

        context = []
        for r in rows:
            kw = kw_map.get(r.keyword_id)
            if not kw:
                continue
            leads = leads_map.get(r.keyword_id)
            context.append({
                "phrase": kw.phrase,
                "clicks": int(r.clicks or 0),
                "spend_rub": round(float(r.spend or 0), 2),
                "leads": int(leads.leads) if leads else 0,
                "sql": int(leads.sql or 0) if leads else 0,
            })
        return context

    async def run_command(self, command_text: str, provider: str) -> dict:
        market_context = await self._build_market_context()

        analysis = AnalysisResult(
            account_id=self.account_id,
            period_start=datetime.utcnow(),
            period_end=datetime.utcnow(),
            summary={
                "source": "campaign_planner",
                "provider": provider,
                "command": command_text,
                "status": "calling_llm",
            },
            problems=[],
        )
        self.db.add(analysis)
        await self.db.flush()

        try:
            draft = llm_providers.call_campaign_planner(provider, command_text, market_context)
        except Exception as e:
            logger.error(f"campaign-planner LLM call failed (provider={provider}): {e}")
            analysis.summary = {**analysis.summary, "status": "llm_error", "error": str(e)}
            await self.db.commit()
            raise CampaignPlannerError(f"Ошибка обращения к {provider}: {e}")

        name = (draft.get("name") or "").strip()
        ad_groups = draft.get("ad_groups") or []
        if not name or not ad_groups:
            analysis.summary = {**analysis.summary, "status": "empty_draft", "llm_raw": draft}
            await self.db.commit()
            raise CampaignPlannerError(
                "ИИ не смог спроектировать кампанию по этой команде (пустое название "
                "или нет ни одной группы) — уточните запрос."
            )

        # Санитайз/дефолты — не полагаемся только на послушность модели.
        try:
            budget = float(draft.get("daily_budget_rub") or 300)
        except (TypeError, ValueError):
            budget = 300.0
        budget = max(100.0, min(budget, 5000.0))  # безопасный коридор для стартового бюджета

        clean_groups = []
        for g in ad_groups[:5]:
            kws = [k.strip() for k in (g.get("keywords") or []) if k and str(k).strip()][:25]
            if not kws:
                continue
            negs = [n.strip() for n in (g.get("negative_keywords") or []) if n and str(n).strip()]
            ads = []
            for ad in (g.get("ads") or [])[:1]:
                if ad.get("title") and ad.get("text") and ad.get("href"):
                    ads.append({
                        "title": ad["title"][:56],
                        "title2": (ad.get("title2") or "")[:30],
                        "text": ad["text"][:81],
                        "href": ad["href"],
                    })
            clean_groups.append({
                "name": (g.get("name") or "Группа")[:255],
                "keywords": kws,
                "negative_keywords": negs,
                "ads": ads,
            })

        if not clean_groups:
            analysis.summary = {**analysis.summary, "status": "no_valid_groups", "llm_raw": draft}
            await self.db.commit()
            raise CampaignPlannerError("Ни одна группа в черновике не содержит ключевых фраз — уточните запрос.")

        payload = {
            "name": name[:250],
            "daily_budget_rub": budget,
            "ad_groups": clean_groups,
            "rationale": draft.get("rationale", ""),
        }

        s = Suggestion(
            account_id=self.account_id,
            analysis_id=analysis.id,
            object_type="campaign_draft",
            object_id=0,
            object_name=name,
            change_type="create_campaign",
            value_before=None,
            value_after=f"Новая кампания «{name}», {len(clean_groups)} групп, бюджет {budget:.0f}₽/день",
            rationale=f"Команда: «{command_text}». {draft.get('rationale', '')} [источник: конструктор кампаний ИИ, {provider}]",
            expected_effect=f"Запуск нового направления: {len(clean_groups)} групп, "
                            f"{sum(len(g['keywords']) for g in clean_groups)} ключей",
            priority="scale",
            status=SuggestionStatus.pending,
            payload=payload,
        )
        self.db.add(s)

        analysis.summary = {
            **analysis.summary,
            "status": "created",
            "suggestion_id": None,  # проставим после flush
        }
        await self.db.flush()
        analysis.summary = {**analysis.summary, "suggestion_id": s.id}
        await self.db.commit()
        await self.db.refresh(s)

        logger.info(
            f"campaign_planner account={self.account_id}: черновик «{name}» "
            f"({len(clean_groups)} групп) → suggestion {s.id}"
        )
        return {
            "status": "created",
            "suggestion_id": s.id,
            "draft": payload,
            "message": (
                f"Черновик кампании «{name}» готов ({len(clean_groups)} групп, "
                f"{sum(len(g['keywords']) for g in clean_groups)} ключей). "
                "Проверьте структуру и одобрите на вкладке «Предложения» — "
                "ничего не создано в Директе, пока вы не подтвердите."
            ),
        }
