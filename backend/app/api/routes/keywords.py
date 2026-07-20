"""Ключевые фразы.

Выделено из монолитного routes.py без изменения логики.
"""
from typing import Optional
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.models import (
    AdGroup, Keyword, KeywordStat, AnalysisResult,
)
from app.api.routes._common import period_dates, calc_delta

router = APIRouter()
logger = logging.getLogger(__name__)


# ─── Keywords ─────────────────────────

@router.get("/accounts/{account_id}/keywords")
async def get_keywords(
    account_id:  int,
    period:      str  = Query("week"),
    campaign_id: Optional[int] = None,
    ad_group_id: Optional[int] = None,
    search:      Optional[str] = None,
    active_only: bool = Query(False),
    limit:       int  = 500,
    date_from:   Optional[str] = Query(None),
    date_to:     Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    curr_start, curr_end, prev_start, prev_end = period_dates(period, date_from, date_to)

    q = select(Keyword).where(Keyword.account_id == account_id)
    if active_only:
        q = q.where(Keyword.status == "ACTIVE")
    if campaign_id:
        q = q.join(AdGroup, AdGroup.id == Keyword.ad_group_id).where(
            AdGroup.campaign_id == campaign_id
        )
    if ad_group_id:
        q = q.where(Keyword.ad_group_id == ad_group_id)
    if search:
        q = q.where(Keyword.phrase.ilike(f"%{search}%"))
    q = q.limit(limit)

    kw_result = await db.execute(q)
    keywords  = kw_result.scalars().all()
    kw_ids    = [k.id for k in keywords]
    if not kw_ids:
        return []

    curr_q = await db.execute(
        select(
            KeywordStat.keyword_id,
            func.sum(KeywordStat.clicks).label("clicks"),
            func.sum(KeywordStat.impressions).label("impressions"),
            func.sum(KeywordStat.spend).label("spend"),
            func.avg(KeywordStat.avg_position).label("avg_position"),
            func.avg(KeywordStat.avg_click_position).label("avg_click_position"),
            func.avg(KeywordStat.traffic_volume).label("traffic_volume"),
            func.avg(KeywordStat.avg_bid).label("avg_bid"),
            func.avg(KeywordStat.avg_cpc).label("avg_cpc_raw"),
            func.avg(KeywordStat.weighted_ctr).label("weighted_ctr"),
            func.sum(KeywordStat.weighted_impressions).label("weighted_impressions"),
            func.avg(KeywordStat.bounce_rate).label("bounce_rate"),
            func.sum(KeywordStat.sessions).label("sessions"),
        )
        .where(and_(
            KeywordStat.keyword_id.in_(kw_ids),
            KeywordStat.date >= curr_start,
            KeywordStat.date <= curr_end,
        ))
        .group_by(KeywordStat.keyword_id)
    )
    curr_map = {r.keyword_id: r for r in curr_q}

    prev_q = await db.execute(
        select(
            KeywordStat.keyword_id,
            func.sum(KeywordStat.clicks).label("clicks"),
            func.avg(KeywordStat.avg_position).label("avg_position"),
            func.avg(KeywordStat.traffic_volume).label("traffic_volume"),
            func.avg(KeywordStat.avg_bid).label("avg_bid"),
            func.avg(KeywordStat.avg_cpc).label("avg_cpc"),
        )
        .where(and_(
            KeywordStat.keyword_id.in_(kw_ids),
            KeywordStat.date >= prev_start,
            KeywordStat.date <= prev_end,
        ))
        .group_by(KeywordStat.keyword_id)
    )
    prev_map = {r.keyword_id: r for r in prev_q}

    sparkline_q = await db.execute(
        select(
            KeywordStat.keyword_id,
            KeywordStat.date,
            KeywordStat.clicks,
        )
        .where(and_(
            KeywordStat.keyword_id.in_(kw_ids),
            KeywordStat.date >= curr_start,
            KeywordStat.date <= curr_end,
        ))
        .order_by(KeywordStat.keyword_id, KeywordStat.date)
    )
    sparkline_map: dict = {}
    for r in sparkline_q:
        sparkline_map.setdefault(r.keyword_id, []).append(
            {"date": r.date.strftime("%Y-%m-%d"), "clicks": int(r.clicks or 0)}
        )

    analysis_result = await db.execute(
        select(AnalysisResult)
        .where(AnalysisResult.account_id == account_id)
        .order_by(desc(AnalysisResult.created_at))
        .limit(1)
    )
    analysis   = analysis_result.scalar_one_or_none()
    signal_map = {}
    if analysis and analysis.problems:
        for p in analysis.problems:
            kid = p.get("keyword_id")
            if kid:
                signal_map[kid] = p

    result = []
    for kw in keywords:
        cs = curr_map.get(kw.id)
        ps = prev_map.get(kw.id)

        clicks       = int(cs.clicks or 0) if cs else 0
        prev_clicks  = int(ps.clicks or 0) if ps else 0
        impressions  = int(cs.impressions or 0) if cs else 0
        spend        = float(cs.spend or 0) if cs else 0
        avg_pos      = round(float(cs.avg_position), 2) if cs and cs.avg_position else None
        avg_cpos     = round(float(cs.avg_click_position), 2) if cs and cs.avg_click_position else None
        traf         = round(float(cs.traffic_volume)) if cs and cs.traffic_volume else None
        avg_bid      = round(float(cs.avg_bid), 2) if cs and cs.avg_bid else None
        w_ctr        = round(float(cs.weighted_ctr), 2) if cs and cs.weighted_ctr else None
        w_impr       = int(cs.weighted_impressions or 0) if cs and cs.weighted_impressions else None
        bounce_rate  = round(float(cs.bounce_rate), 1) if cs and cs.bounce_rate else None
        sessions     = int(cs.sessions or 0) if cs and cs.sessions else None

        click_delta  = calc_delta(clicks, prev_clicks)
        prev_bid     = float(ps.avg_bid) if ps and ps.avg_bid else None
        bid_delta    = calc_delta(avg_bid, prev_bid)
        prev_pos     = float(ps.avg_position) if ps and ps.avg_position else None
        pos_delta    = calc_delta(prev_pos, avg_pos, invert=True) if prev_pos and avg_pos else None

        avg_cpc = round(spend / clicks, 2) if clicks > 0 else None
        ctr     = round(clicks / impressions * 100, 2) if impressions > 0 else None

        pos_gap = round(avg_cpos - avg_pos, 2) if avg_pos and avg_cpos else None

        sig = signal_map.get(kw.id)
        recommended_bid = None
        if sig and sig.get("recommended_bid"):
            recommended_bid = sig["recommended_bid"]
        elif kw.current_bid:
            cb = float(kw.current_bid)
            if avg_pos and avg_pos > 3:
                recommended_bid = round(cb * 1.3, 2)
            elif avg_pos and avg_pos < 1.5:
                recommended_bid = round(cb * 0.9, 2)

        traffic_quality = None
        if bounce_rate is not None and bounce_rate > 0:
            q_score = (
                (1 - bounce_rate / 100) * 0.5 +
                min((sessions or 0) / max(clicks, 1), 1.0) * 0.3 +
                min((ctr or 0) / 5.0, 1.0) * 0.2
            )
            traffic_quality = round(min(q_score * 100, 100), 1)

        result.append({
            "id":               kw.id,
            "phrase":           kw.phrase,
            "status":           kw.status,
            "current_bid":      float(kw.current_bid) if kw.current_bid else None,
            "avg_bid":          avg_bid,
            "recommended_bid":  recommended_bid,
            "bid_delta":        bid_delta,
            "clicks":           clicks,
            "impressions":      impressions,
            "spend":            round(spend, 2),
            "ctr":              ctr,
            "avg_cpc":          avg_cpc,
            "avg_position":     avg_pos,
            "avg_click_position": avg_cpos,
            "click_position_gap": pos_gap,
            "traffic_volume":   traf,
            "weighted_ctr":     w_ctr,
            "weighted_impressions": w_impr,
            "bounce_rate":      bounce_rate,
            "sessions":         sessions,
            "click_delta":      click_delta,
            "position_delta":   pos_delta,
            "traffic_quality":  traffic_quality,
            "signal":           sig,
            "problem":          sig,
            "sparkline":        sparkline_map.get(kw.id, []),
        })

    result.sort(key=lambda x: (0 if x["signal"] else 1, -(x["spend"] or 0)))
    return result
