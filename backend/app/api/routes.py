"""
API роуты для фронтенда.
"""
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, func
from pydantic import BaseModel

from app.db.database import get_db
from app.models.models import (
    Account, Campaign, Keyword, AnalysisResult, Suggestion,
    SuggestionStatus, Hypothesis, KeywordMetrics, KeywordStat
)

router = APIRouter()


# ─── Аккаунты ─────────────────────────────────────────────────────────────────

class AccountCreate(BaseModel):
    name: str
    yandex_login: str
    oauth_token: str
    metrika_counter_id: Optional[str] = None
    target_cpl: Optional[float] = None
    target_cpql: Optional[float] = None


class AccountResponse(BaseModel):
    id: int
    name: str
    yandex_login: str
    metrika_counter_id: Optional[str]
    target_cpl: Optional[float]
    target_cpql: Optional[float]
    is_active: bool
    last_sync_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("/accounts", response_model=list[AccountResponse])
async def list_accounts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).order_by(Account.created_at.desc()))
    return result.scalars().all()


@router.post("/accounts", response_model=AccountResponse)
async def create_account(data: AccountCreate, db: AsyncSession = Depends(get_db)):
    account = Account(**data.model_dump())
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


@router.post("/accounts/{account_id}/sync")
async def trigger_sync(account_id: int, db: AsyncSession = Depends(get_db)):
    """Запустить ручной сбор данных и анализ"""
    from app.core.tasks import collect_account_data
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Account not found")
    collect_account_data.delay(account_id)
    return {"status": "queued", "message": "Сбор данных запущен. Анализ будет доступен через 2–5 минут."}


# ─── Дашборд — сводная аналитика ──────────────────────────────────────────────

@router.get("/accounts/{account_id}/dashboard")
async def get_dashboard(account_id: int, db: AsyncSession = Depends(get_db)):
    """Главный дашборд: последний анализ + KPI + топ-проблемы"""
    # Последний анализ
    analysis_result = await db.execute(
        select(AnalysisResult)
        .where(AnalysisResult.account_id == account_id)
        .order_by(desc(AnalysisResult.created_at))
        .limit(1)
    )
    analysis = analysis_result.scalar_one_or_none()

    # Статистика предложений
    pending_count = await db.execute(
        select(func.count(Suggestion.id)).where(
            and_(Suggestion.account_id == account_id, Suggestion.status == SuggestionStatus.pending)
        )
    )
    today_count = await db.execute(
        select(func.count(Suggestion.id)).where(
            and_(
                Suggestion.account_id == account_id,
                Suggestion.status == SuggestionStatus.pending,
                Suggestion.priority == "today",
            )
        )
    )

    # Кампании с базовыми метриками
    campaigns_result = await db.execute(
        select(Campaign).where(Campaign.account_id == account_id, Campaign.is_active == True)
    )
    campaigns = campaigns_result.scalars().all()

    return {
        "account_id": account_id,
        "last_analysis": {
            "id": analysis.id if analysis else None,
            "created_at": analysis.created_at.isoformat() if analysis else None,
            "summary": analysis.summary if analysis else None,
            "problems": analysis.problems if analysis else [],
            "opportunities": analysis.opportunities if analysis else [],
        } if analysis else None,
        "suggestions_stats": {
            "pending": pending_count.scalar(),
            "urgent_today": today_count.scalar(),
        },
        "campaigns_count": len(campaigns),
    }


# ─── Кампании ─────────────────────────────────────────────────────────────────

@router.get("/accounts/{account_id}/campaigns")
async def get_campaigns(account_id: int, db: AsyncSession = Depends(get_db)):
    """Список кампаний с метриками из последнего анализа"""
    campaigns_result = await db.execute(
        select(Campaign).where(Campaign.account_id == account_id)
        .order_by(Campaign.name)
    )
    campaigns = campaigns_result.scalars().all()

    # Получить последний анализ
    analysis_result = await db.execute(
        select(AnalysisResult)
        .where(AnalysisResult.account_id == account_id)
        .order_by(desc(AnalysisResult.created_at))
        .limit(1)
    )
    analysis = analysis_result.scalar_one_or_none()

    result = []
    for c in campaigns:
        # Посчитать ключи с предложениями
        suggestions_count = await db.execute(
            select(func.count(Suggestion.id))
            .join(Keyword, Keyword.id == Suggestion.object_id)
            .join(Campaign, Campaign.id == Keyword.ad_group_id)  # упрощение
            .where(
                and_(
                    Suggestion.account_id == account_id,
                    Suggestion.status == SuggestionStatus.pending,
                )
            )
        )

        result.append({
            "id": c.id,
            "direct_id": c.direct_id,
            "name": c.name,
            "campaign_type": c.campaign_type,
            "status": c.status,
            "is_active": c.is_active,
        })
    return result


# ─── Предложения ──────────────────────────────────────────────────────────────

@router.get("/accounts/{account_id}/suggestions")
async def get_suggestions(
    account_id: int,
    priority: Optional[str] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Список предложений по изменениям"""
    filters = [Suggestion.account_id == account_id]
    if priority:
        filters.append(Suggestion.priority == priority)
    if status:
        filters.append(Suggestion.status == status)
    else:
        filters.append(Suggestion.status == SuggestionStatus.pending)

    result = await db.execute(
        select(Suggestion)
        .where(and_(*filters))
        .order_by(
            Suggestion.priority.asc(),  # today first
            Suggestion.created_at.desc(),
        )
    )
    suggestions = result.scalars().all()

    priority_order = {"today": 0, "this_week": 1, "month": 2, "scale": 3}
    return [
        {
            "id": s.id,
            "object_type": s.object_type,
            "object_name": s.object_name,
            "change_type": s.change_type,
            "value_before": s.value_before,
            "value_after": s.value_after,
            "rationale": s.rationale,
            "expected_effect": s.expected_effect,
            "priority": s.priority,
            "priority_order": priority_order.get(s.priority, 99),
            "status": s.status,
            "created_at": s.created_at.isoformat(),
        }
        for s in suggestions
    ]


class SuggestionAction(BaseModel):
    action: str  # approve / reject / modify
    reject_reason: Optional[str] = None
    new_value: Optional[str] = None
    approved_by: Optional[str] = "директолог"


@router.post("/suggestions/{suggestion_id}/action")
async def action_suggestion(
    suggestion_id: int,
    data: SuggestionAction,
    db: AsyncSession = Depends(get_db),
):
    """Одобрить / отклонить / изменить предложение"""
    result = await db.execute(select(Suggestion).where(Suggestion.id == suggestion_id))
    suggestion = result.scalar_one_or_none()
    if not suggestion:
        raise HTTPException(404, "Suggestion not found")

    if data.action == "approve":
        suggestion.status = SuggestionStatus.approved
        suggestion.approved_by = data.approved_by
        suggestion.applied_at = datetime.utcnow()

        # Создать гипотезу для трекинга
        from datetime import timedelta
        h = Hypothesis(
            account_id=suggestion.account_id,
            suggestion_id=suggestion.id,
            applied_at=datetime.utcnow(),
            track_until=datetime.utcnow() + timedelta(days=7),
        )
        db.add(h)

        # Инструкция для ручного применения (Фаза 1)
        instruction = _build_instruction(suggestion)

    elif data.action == "reject":
        suggestion.status = SuggestionStatus.rejected
        suggestion.reject_reason = data.reject_reason
        instruction = None

    elif data.action == "modify":
        suggestion.value_after = data.new_value or suggestion.value_after
        suggestion.status = SuggestionStatus.approved
        suggestion.approved_by = data.approved_by
        suggestion.applied_at = datetime.utcnow()
        instruction = _build_instruction(suggestion)

    else:
        raise HTTPException(400, "action must be approve/reject/modify")

    await db.commit()
    return {"status": "ok", "instruction": instruction}


def _build_instruction(suggestion: Suggestion) -> dict:
    """Инструкция для ручного применения в Директе"""
    action_labels = {
        "bid_raise": "Поднять ставку",
        "bid_lower": "Снизить ставку",
        "bid_hold": "Ставку не менять",
        "strategy_cpa": "Перевести на CPA",
        "add_negatives": "Добавить минус-слова",
        "disable_keyword": "Отключить ключ",
        "expand_semantics": "Расширить семантику",
    }
    return {
        "action": action_labels.get(suggestion.change_type, suggestion.change_type),
        "object": suggestion.object_name,
        "from": suggestion.value_before,
        "to": suggestion.value_after,
        "steps": [
            "Открыть Яндекс Директ → Все кампании",
            f"Найти ключевое слово: «{suggestion.object_name}»",
            f"{action_labels.get(suggestion.change_type, 'Применить')}: {suggestion.value_before} → {suggestion.value_after}",
            "Сохранить изменения",
        ],
    }


# ─── Анализы и гипотезы ───────────────────────────────────────────────────────

@router.get("/accounts/{account_id}/analyses")
async def get_analyses(account_id: int, limit: int = 10, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AnalysisResult)
        .where(AnalysisResult.account_id == account_id)
        .order_by(desc(AnalysisResult.created_at))
        .limit(limit)
    )
    analyses = result.scalars().all()
    return [
        {
            "id": a.id,
            "period_start": a.period_start.isoformat(),
            "period_end": a.period_end.isoformat(),
            "created_at": a.created_at.isoformat(),
            "summary": a.summary,
            "problems_count": len(a.problems or []),
            "opportunities_count": len(a.opportunities or []),
        }
        for a in analyses
    ]


@router.get("/accounts/{account_id}/hypotheses")
async def get_hypotheses(account_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Hypothesis)
        .where(Hypothesis.account_id == account_id)
        .order_by(desc(Hypothesis.created_at))
        .limit(50)
    )
    hypotheses = result.scalars().all()
    return [
        {
            "id": h.id,
            "applied_at": h.applied_at.isoformat(),
            "track_until": h.track_until.isoformat(),
            "verdict": h.verdict,
            "delta_percent": float(h.delta_percent) if h.delta_percent else None,
            "report": h.report,
            "metrics_before": h.metrics_before,
            "metrics_after": h.metrics_after,
        }
        for h in hypotheses
    ]


# ─── Данные по ключам ────────────────────────────────────────────────────────

@router.get("/accounts/{account_id}/keywords")
async def get_keywords(
    account_id: int,
    campaign_id: Optional[int] = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    from app.models.models import AdGroup
    filters = [Keyword.account_id == account_id]

    result = await db.execute(
        select(Keyword).where(and_(*filters)).limit(limit)
    )
    keywords = result.scalars().all()

    # Загрузить последние метрики
    analysis_result = await db.execute(
        select(AnalysisResult)
        .where(AnalysisResult.account_id == account_id)
        .order_by(desc(AnalysisResult.created_at))
        .limit(1)
    )
    analysis = analysis_result.scalar_one_or_none()

    metrics_map = {}
    if analysis:
        metrics_result = await db.execute(
            select(KeywordMetrics).where(KeywordMetrics.analysis_id == analysis.id)
        )
        for m in metrics_result.scalars().all():
            metrics_map[m.keyword_id] = m

    return [
        {
            "id": kw.id,
            "phrase": kw.phrase,
            "current_bid": float(kw.current_bid) if kw.current_bid else None,
            "status": kw.status,
            "metrics": {
                "clicks": metrics_map[kw.id].clicks if kw.id in metrics_map else None,
                "spend": float(metrics_map[kw.id].spend) if kw.id in metrics_map else None,
                "leads": metrics_map[kw.id].leads if kw.id in metrics_map else None,
                "cr_click_lead": float(metrics_map[kw.id].cr_click_lead or 0) * 100 if kw.id in metrics_map else None,
                "cpl": float(metrics_map[kw.id].cpl) if kw.id in metrics_map and metrics_map[kw.id].cpl else None,
                "recommended_bid": float(metrics_map[kw.id].recommended_bid) if kw.id in metrics_map and metrics_map[kw.id].recommended_bid else None,
                "is_significant": metrics_map[kw.id].is_significant if kw.id in metrics_map else False,
            } if kw.id in metrics_map else None,
        }
        for kw in keywords
    ]


@router.get("/accounts/{account_id}/rules")
async def get_rules(account_id: int, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import or_
    from app.models.models import Rule
    result = await db.execute(
        select(Rule).where(
            and_(
                Rule.is_active == True,
                or_(Rule.account_id == account_id, Rule.account_id == None),
            )
        ).order_by(Rule.account_id.desc().nullslast())
    )
    rules = result.scalars().all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "condition_type": r.condition_type,
            "action_type": r.action_type,
            "priority": r.priority,
            "description": r.description,
            "is_global": r.account_id is None,
        }
        for r in rules
    ]
