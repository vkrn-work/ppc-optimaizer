"""Результаты анализа и рекомендации.

Выделено из монолитного routes.py без изменения логики.
"""
from datetime import datetime, timedelta
from typing import Optional
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.models import (
    AnalysisResult, Suggestion, Hypothesis, SuggestionStatus,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ─── Analysis ──────────────────────────

@router.get("/accounts/{account_id}/analyses")
async def get_analyses(account_id: int, limit: int = 10, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AnalysisResult)
        .where(AnalysisResult.account_id == account_id)
        .order_by(desc(AnalysisResult.created_at))
        .limit(limit)
    )
    analyses = result.scalars().all()
    return [{
        "id": a.id,
        "created_at": a.created_at.isoformat(),
        "period_start": a.period_start.isoformat() if a.period_start else None,
        "period_end":   a.period_end.isoformat()   if a.period_end   else None,
        "summary":      a.summary,
        "problems":     a.problems or [],
        "opportunities": a.opportunities or [],
    } for a in analyses]


# ─── Suggestions ─────────────────────────────

@router.get("/accounts/{account_id}/suggestions")
async def get_suggestions(
    account_id: int,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    # CHANGED v2.0: читаем реальную таблицу suggestions (раньше собирались на лету
    # из analysis.problems с фиктивными id p_0/o_1 — из-за чего таблица и весь
    # SuggestionGenerator были мёртвым кодом, а аппрув не связывался с гипотезой).
    q = select(Suggestion).where(Suggestion.account_id == account_id)
    if status:
        try:
            q = q.where(Suggestion.status == SuggestionStatus(status))
        except ValueError:
            pass
    else:
        q = q.where(Suggestion.status == SuggestionStatus.pending)
    q = q.order_by(desc(Suggestion.created_at))
    result = await db.execute(q)
    rows = result.scalars().all()

    prio_sev = {"today": "critical", "this_week": "warning", "month": "info", "scale": "info"}
    return [{
        "id":            s.id,
        "object_type":   s.object_type,
        "object_id":     s.object_id,
        "keyword_id":    s.object_id if s.object_type == "keyword" else None,
        "object_name":   s.object_name,
        "phrase":        s.object_name,
        "change_type":   s.change_type,
        "value_before":  s.value_before,
        "value_after":   s.value_after,
        "description":   s.rationale,
        "hypothesis":    (s.rationale.split("Гипотеза:")[-1].strip()
                          if s.rationale and "Гипотеза:" in s.rationale else None),
        "action":        s.expected_effect,
        "expected_effect": s.expected_effect,
        "rationale":     s.rationale,
        "recommended_bid": (float(s.value_after.replace("₽", ""))
                            if s.value_after and s.value_after.replace("₽", "").replace(".", "").isdigit()
                            else None),
        "priority":      s.priority,
        "severity":      prio_sev.get(s.priority, "warning"),
        "status":        s.status.value,
        "payload":       s.payload,  # v1.7.0: черновик create_campaign (группы/ключи/объявления)
    } for s in rows]


@router.post("/suggestions/{suggestion_id}/action")
async def action_suggestion(suggestion_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    # CHANGED v2.0: работаем с реальным числовым id из таблицы suggestions.
    # accept → status=approved + Hypothesis СО ССЫЛКОЙ suggestion_id (иначе трекер
    # гипотез не мог их оценивать). reject → status=rejected + причина.
    action = data.get("action", "accept")
    reason = data.get("reason")

    res = await db.execute(select(Suggestion).where(Suggestion.id == int(suggestion_id)))
    s = res.scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Suggestion not found")

    if action == "reject":
        s.status = SuggestionStatus.rejected
        s.reject_reason = reason
        await db.commit()
        return {"status": "rejected", "suggestion_id": s.id}

    s.status = SuggestionStatus.approved
    s.approved_by = data.get("approved_by") or "director"
    hypothesis = Hypothesis(
        account_id=s.account_id,
        suggestion_id=s.id,
        object_type=s.object_type,
        object_id=s.object_id,
        description=f"{s.object_name}: {s.rationale or ''}",
        change_description=s.expected_effect or s.change_type,
        forecast=s.expected_effect,
        source="algorithm",
        verdict=None,
        applied_at=datetime.utcnow(),
        track_until=datetime.utcnow() + timedelta(days=7),
    )
    db.add(hypothesis)
    await db.commit()
    await db.refresh(hypothesis)
    return {"status": "approved", "suggestion_id": s.id, "hypothesis_id": hypothesis.id}
