"""v1.7.5: роуты диагностики и пересчёта атрибуции заявок.

Отвечают на вопрос «точно ли вся стата идёт в анализ» без запуска LLM:
видно, сколько заявок разнесено каким уровнем каскада и почему остальные
не разнеслись.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, and_, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.models import Account, Lead, Keyword, Campaign
from app.importers.lead_attribution import reattribute_account, build_matchers

router = APIRouter()
logger = logging.getLogger(__name__)


async def _require_account(db: AsyncSession, account_id: int) -> Account:
    res = await db.execute(select(Account).where(Account.id == account_id))
    account = res.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Account not found")
    return account


@router.post("/accounts/{account_id}/leads/reattribute")
async def reattribute_leads(
    account_id: int,
    only_unmatched: bool = Query(False, description="Трогать только непривязанные заявки"),
    db: AsyncSession = Depends(get_db),
):
    """Пересчитывает разноску заявок по ключам/кампаниям по актуальным данным Директа.

    Нужен, если CRM-выгрузка была загружена раньше, чем собрались поисковые
    запросы: такие заявки остаются без keyword_id навсегда, пока не пересчитать.
    С v1.7.5 то же самое автоматически выполняется перед каждым ИИ-анализом.
    """
    await _require_account(db, account_id)
    stats = await reattribute_account(db, account_id, only_unmatched=only_unmatched)
    return {"status": "ok", **stats}


@router.get("/accounts/{account_id}/leads/attribution")
async def attribution_report(
    account_id: int,
    period_days: int = Query(28, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """Отчёт о качестве разноски заявок — без запуска LLM."""
    await _require_account(db, account_id)
    period_start = datetime.utcnow() - timedelta(days=period_days)

    rows = (await db.execute(
        select(
            Lead.matched_by,
            func.count(Lead.id).label("leads"),
            func.sum(case((Lead.is_mql.is_(True), 1), else_=0)).label("mql"),
            func.sum(case((Lead.is_sql.is_(True), 1), else_=0)).label("sql"),
        )
        .where(and_(Lead.account_id == account_id, Lead.created_at >= period_start))
        .group_by(Lead.matched_by)
    )).all()

    by_method = {
        (r.matched_by or "unmatched"): {
            "leads": int(r.leads or 0),
            "mql": int(r.mql or 0),
            "sql": int(r.sql or 0),
        }
        for r in rows
    }
    total = sum(v["leads"] for v in by_method.values())
    with_keyword = sum(v["leads"] for k, v in by_method.items()
                       if k in ("ad_id", "search_query", "phrase"))
    with_campaign = total - by_method.get("unmatched", {}).get("leads", 0)

    # Примеры непривязанных — по ним видно, какой уровень каскада не сработал.
    unmatched_rows = (await db.execute(
        select(Lead.id, Lead.raw_status, Lead.utm_campaign, Lead.utm_term,
               Lead.source_raw, Lead.created_at)
        .where(and_(
            Lead.account_id == account_id,
            Lead.created_at >= period_start,
            Lead.keyword_id.is_(None),
            Lead.campaign_id.is_(None),
        ))
        .order_by(Lead.created_at.desc())
        .limit(20)
    )).all()

    matchers = await build_matchers(db, account_id)

    return {
        "period_days": period_days,
        "leads_total": total,
        "leads_with_keyword": with_keyword,
        "leads_with_campaign": with_campaign,
        "keyword_coverage_pct": round(with_keyword / total * 100, 1) if total else None,
        "campaign_coverage_pct": round(with_campaign / total * 100, 1) if total else None,
        "by_match_method": by_method,
        # Если здесь search_queries=0 — матчингу не на чем строиться, и дело не
        # в заявках, а в несобранных данных Директа.
        "matchers": matchers.stats(),
        "unmatched_examples": [
            {
                "lead_id": r.id,
                "status": r.raw_status,
                "utm_campaign": r.utm_campaign,
                "utm_term": r.utm_term,
                "source_raw": r.source_raw,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in unmatched_rows
        ],
    }
