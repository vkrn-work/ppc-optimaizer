"""Ежедневная статистика и дашборд.

Выделено из монолитного routes.py без изменения логики.
"""
from datetime import datetime, timedelta
from typing import Optional
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.models import (
    Account, Campaign, AdGroup, Keyword, KeywordStat,
    AnalysisResult, Suggestion, Lead, SuggestionStatus,
    MetrikaSnapshot,
)
from app.api.routes._common import period_dates, agg_kw_stats, agg_leads, mk_crm_kpi_block, mk_kpi_block, get_daily_series

router = APIRouter()
logger = logging.getLogger(__name__)


# ─── Daily Stats ────────────────────────────

@router.get("/accounts/{account_id}/daily-stats")
async def get_daily_stats(
    account_id: int,
    date_from: str = Query(..., description="YYYY-MM-DD"),
    date_to: str   = Query(..., description="YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
):
    try:
        dt_from = datetime.strptime(date_from, "%Y-%m-%d")
        dt_to   = datetime.strptime(date_to,   "%Y-%m-%d") + timedelta(days=1)
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD")
    rows = await get_daily_series(db, account_id, dt_from, dt_to)
    return {"date_from": date_from, "date_to": date_to, "rows": rows}


@router.get("/accounts/{account_id}/campaigns/{campaign_id}/daily-stats")
async def get_campaign_daily_stats(
    account_id: int,
    campaign_id: int,
    date_from: str = Query(..., description="YYYY-MM-DD"),
    date_to: str   = Query(..., description="YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
):
    try:
        dt_from = datetime.strptime(date_from, "%Y-%m-%d")
        dt_to   = datetime.strptime(date_to,   "%Y-%m-%d") + timedelta(days=1)
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD")
    rows = await get_daily_series(db, account_id, dt_from, dt_to, campaign_id=campaign_id)
    return {"campaign_id": campaign_id, "date_from": date_from, "date_to": date_to, "rows": rows}


# ─── Dashboard ──────────────────────────

@router.get("/accounts/{account_id}/dashboard")
async def get_dashboard(
    account_id: int,
    period: str = Query("week"),
    date_from:    Optional[str] = Query(None),
    date_to:      Optional[str] = Query(None),
    compare_from: Optional[str] = Query(None),
    compare_to:   Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    curr_start, curr_end, prev_start, prev_end = period_dates(
        period, date_from, date_to, compare_from, compare_to
    )

    acc_result = await db.execute(select(Account).where(Account.id == account_id))
    account = acc_result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Account not found")

    curr_kpi = await agg_kw_stats(db, account_id, curr_start, curr_end)
    prev_kpi = await agg_kw_stats(db, account_id, prev_start, prev_end)
    kpi_with_deltas = mk_kpi_block(curr_kpi, prev_kpi)

    # v1.7.0: CRM-воронка (лиды/MQL/SQL) на дашборде — раньше данные из /crm-import
    # копились в БД, но на Главную не попадали (был статичный плейсхолдер).
    curr_leads_kpi = await agg_leads(db, account_id, curr_start, curr_end, curr_kpi["spend"])
    prev_leads_kpi = await agg_leads(db, account_id, prev_start, prev_end, prev_kpi["spend"])
    crm_kpi = mk_crm_kpi_block(curr_leads_kpi, prev_leads_kpi)
    total_leads_check = await db.execute(
        select(func.count(Lead.id)).where(Lead.account_id == account_id)
    )
    has_crm_data = (total_leads_check.scalar() or 0) > 0

    camp_count = await db.execute(
        select(func.count(Campaign.id)).where(
            and_(Campaign.account_id == account_id, Campaign.is_active == True)
        )
    )
    active_campaigns = camp_count.scalar() or 0
    total_camp = await db.execute(
        select(func.count(Campaign.id)).where(Campaign.account_id == account_id)
    )
    total_campaigns = total_camp.scalar() or 0

    analysis_result = await db.execute(
        select(AnalysisResult)
        .where(AnalysisResult.account_id == account_id)
        .order_by(desc(AnalysisResult.created_at))
        .limit(1)
    )
    analysis = analysis_result.scalar_one_or_none()

    today_suggestions = await db.execute(
        select(func.count(Suggestion.id)).where(
            Suggestion.account_id == account_id,
            Suggestion.status == SuggestionStatus.pending,
        )
    )

    metrika_result = await db.execute(
        select(MetrikaSnapshot)
        .where(MetrikaSnapshot.account_id == account_id)
        .order_by(desc(MetrikaSnapshot.date))
        .limit(2)
    )
    metrika_rows = metrika_result.scalars().all()
    metrika      = metrika_rows[0] if metrika_rows else None
    metrika_prev = metrika_rows[1] if len(metrika_rows) > 1 else None

    behavior = {}
    if metrika and metrika.data:
        all_by_day     = metrika.data.get("by_day", [])
        curr_start_str = curr_start.date().isoformat()
        curr_end_str   = curr_end.date().isoformat()
        period_by_day  = [d for d in all_by_day if curr_start_str <= d.get("date", "") <= curr_end_str]
        prev_by_day    = [d for d in all_by_day if prev_start.date().isoformat() <= d.get("date", "") <= prev_end.date().isoformat()]

        def avg_f(days, key):
            vals = [float(d[key]) for d in days if d.get(key) is not None]
            return round(sum(vals) / len(vals), 2) if vals else None

        def sum_f(days, key):
            return sum(float(d.get(key) or 0) for d in days)

        curr_visits   = sum_f(period_by_day, "visits")
        curr_bounce   = avg_f(period_by_day, "bounceRate")
        curr_duration = avg_f(period_by_day, "avgVisitDurationSeconds")
        curr_depth    = avg_f(period_by_day, "pageDepth")

        if not period_by_day:
            s = metrika.data.get("summary", {})
            curr_visits   = s.get("visits")
            curr_bounce   = s.get("bounceRate")
            curr_duration = s.get("avgVisitDurationSeconds")
            curr_depth    = s.get("pageDepth")

        prev_visits   = sum_f(prev_by_day, "visits")   if prev_by_day else None
        prev_bounce   = avg_f(prev_by_day, "bounceRate") if prev_by_day else None
        prev_duration = avg_f(prev_by_day, "avgVisitDurationSeconds") if prev_by_day else None
        prev_depth    = avg_f(prev_by_day, "pageDepth") if prev_by_day else None

        if metrika_prev and metrika_prev.data and not prev_by_day:
            ps = metrika_prev.data.get("summary", {})
            prev_visits   = ps.get("visits")
            prev_bounce   = ps.get("bounceRate")
            prev_duration = ps.get("avgVisitDurationSeconds")
            prev_depth    = ps.get("pageDepth")

        qs = None
        if curr_bounce is not None:
            b  = (1 - (curr_bounce or 0) / 100) * 0.4
            t  = min((curr_duration or 0) / 180, 1) * 0.3
            d  = min((curr_depth or 0) / 3, 1) * 0.2
            qs = round((b + t + d) * 100 / 0.9)

        def mk_m_delta(curr_v, prev_v, invert=False):
            if not prev_v or prev_v == 0 or curr_v is None:
                return None
            d = (curr_v - prev_v) / abs(prev_v) * 100
            return {"value": round(d, 1), "is_good": (d < 0 if invert else d > 0)}

        behavior = {
            "has_metrika":       True,
            "visits":            curr_visits,
            "visits_delta":      mk_m_delta(curr_visits, prev_visits),
            "visits_prev":       prev_visits,
            "bounce_rate":       curr_bounce,
            "bounce_delta":      mk_m_delta(curr_bounce, prev_bounce, invert=True),
            "bounce_prev":       prev_bounce,
            "page_depth":        curr_depth,
            "page_depth_prev":   prev_depth,
            "page_depth_delta":  mk_m_delta(curr_depth, prev_depth),
            "avg_duration":      curr_duration,
            "avg_duration_prev": prev_duration,
            "duration_delta":    mk_m_delta(curr_duration, prev_duration),
            "quality_score":     qs,
            "by_day":            period_by_day,
            "devices":           metrika.data.get("devices", []),
            "regions":           metrika.data.get("regions", [])[:10],
            "by_weekday":        metrika.data.get("by_weekday", []),
            "by_hour":           metrika.data.get("by_hour", []),
            "landings":          metrika.data.get("landings", [])[:10],
            "browsers":          metrika.data.get("browsers", [])[:10],
        }

    daily_stats = await get_daily_series(db, account_id, curr_start, curr_end)

    top_campaigns_q = await db.execute(
        select(
            Campaign.id, Campaign.name, Campaign.strategy_type, Campaign.direct_id,
            func.sum(KeywordStat.spend).label("spend"),
            func.sum(KeywordStat.clicks).label("clicks"),
            func.avg(KeywordStat.avg_position).label("avg_position"),
        )
        .join(AdGroup, AdGroup.campaign_id == Campaign.id)
        .join(Keyword, Keyword.ad_group_id == AdGroup.id)
        .join(KeywordStat, KeywordStat.keyword_id == Keyword.id)
        .where(
            Campaign.account_id == account_id,
            KeywordStat.date >= curr_start,
            KeywordStat.date <= curr_end,
        )
        .group_by(Campaign.id, Campaign.name, Campaign.strategy_type, Campaign.direct_id)
        .order_by(desc("spend"))
        .limit(5)
    )
    top_campaigns = [{
        "id": r.id,
        "direct_id": r.direct_id,
        "name": r.name,
        "strategy_type": r.strategy_type,
        "spend": round(float(r.spend or 0), 2),
        "clicks": int(r.clicks or 0),
        "avg_position": round(float(r.avg_position), 2) if r.avg_position else None,
    } for r in top_campaigns_q]

    is_custom = bool(date_from and date_to)
    return {
        "account_id": account_id,
        "period": period,
        "is_custom_range": is_custom,
        "period_dates": {
            "curr_start": curr_start.date().isoformat(),
            "curr_end":   (curr_end - timedelta(days=1)).date().isoformat(),
            "prev_start": prev_start.date().isoformat(),
            "prev_end":   (prev_end - timedelta(days=1)).date().isoformat(),
        },
        "ad_kpi":            kpi_with_deltas,
        "crm_kpi":           crm_kpi,
        "has_crm_data":      has_crm_data,
        "active_campaigns":  active_campaigns,
        "total_campaigns":   total_campaigns,
        "behavior":          behavior,
        "problems":          analysis.problems if analysis else [],
        "opportunities":     analysis.opportunities if analysis else [],
        "analysis_at":       analysis.created_at.isoformat() if analysis else None,
        "analysis_summary":  analysis.summary if analysis else {},
        "suggestions_pending": today_suggestions.scalar() or 0,
        "top_campaigns":     top_campaigns,
        "daily_stats":       daily_stats,
        "period_label": {
            "yesterday": "Вчера vs среднее за 14 дней",
            "3d":   "3 дня vs предыдущие 3 дня",
            "week": "7 дней vs предыдущие 7 дней",
            "month": "30 дней vs предыдущие 30 дней",
        }.get(period, "Произвольный период" if is_custom else period),
    }
