"""Гипотезы и правила.

Выделено из монолитного routes.py без изменения логики.
"""
from datetime import datetime, timedelta
from typing import Optional
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.models import (
    Hypothesis, Rule,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ─── Hypotheses ────────────────────────────

class HypothesisCreate(BaseModel):
    object_type: str = "keyword"
    object_id: Optional[int] = None
    keyword_id: Optional[int] = None
    description: Optional[str] = None
    phrase: Optional[str] = None
    change_description: str
    forecast: Optional[str] = None
    source: str = "manual"
    problem_type: Optional[str] = None
    severity: Optional[str] = None
    priority: Optional[str] = None


@router.get("/accounts/{account_id}/hypotheses")
async def get_hypotheses(account_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Hypothesis)
        .where(Hypothesis.account_id == account_id)
        .order_by(desc(Hypothesis.applied_at))
    )
    hyps = result.scalars().all()

    def h_status(h):
        # CHANGED: убраны мёртвые ветки positive/negative (нет в enum).
        v = h.verdict.value if h.verdict else "pending"
        return {
            "pending":      "planned",
            "confirmed":    "success",
            "rejected":     "failed",
            "neutral":      "neutral",
            "insufficient": "insufficient",
        }.get(v, v)

    return [{
        "id":               h.id,
        "object_type":      h.object_type,
        "phrase":           h.description.split(":")[0] if h.description and ":" in h.description else h.description,
        "description":      h.description,
        "change_description": h.change_description,
        "forecast":         h.forecast,
        "source":           h.source,
        "status":           h_status(h),
        "verdict":          h.verdict.value if h.verdict else None,
        "created_at":       h.applied_at.isoformat() if h.applied_at else None,
        "check_after":      h.track_until.isoformat() if h.track_until else None,
        "metrics_before":   h.metrics_before,
        "metrics_after":    h.metrics_after,
        "report":           h.report,
        "delta_percent":    float(h.delta_percent) if h.delta_percent else None,
    } for h in hyps]


@router.post("/accounts/{account_id}/hypotheses")
async def create_hypothesis(account_id: int, data: HypothesisCreate, db: AsyncSession = Depends(get_db)):
    description = data.description or (
        f"{data.phrase}: {data.change_description}" if data.phrase else data.change_description
    )
    hypothesis = Hypothesis(
        account_id=account_id,
        description=description,
        change_description=data.change_description,
        forecast=data.forecast,
        object_type=data.object_type,
        object_id=data.keyword_id or data.object_id,
        source=data.source,
        verdict=None,
        applied_at=datetime.utcnow(),
        track_until=datetime.utcnow() + timedelta(days=7),
    )
    db.add(hypothesis)
    await db.commit()
    await db.refresh(hypothesis)
    return {"id": hypothesis.id, "status": "created"}


# ─── Rules ──────────────────────────────

@router.get("/accounts/{account_id}/rules")
async def get_rules(account_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Rule).where(Rule.account_id == account_id).order_by(Rule.priority)
    )
    rules = result.scalars().all()
    return [{
        "id":             r.id,
        "name":           r.name,
        "condition_type": r.condition_type,
        "action_type":    r.action_type,
        "action_params":  r.action_params,
        "priority":       r.priority,
        "is_active":      r.is_active,
    } for r in rules]
