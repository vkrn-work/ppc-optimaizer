"""Кампании и группы объявлений.

Выделено из монолитного routes.py без изменения логики.
"""
from typing import Optional
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.models import (
    Campaign, AdGroup, Keyword, KeywordStat, AnalysisResult,
)
from app.api.routes._common import period_dates, calc_delta

router = APIRouter()
logger = logging.getLogger(__name__)


# ─── Campaigns ──────────────────────────

@router.get("/accounts/{account_id}/campaigns")
async def get_campaigns(
    account_id:   int,
    period:       str  = Query("week"),
    active_only:  bool = Query(False),
    date_from:    Optional[str] = Query(None),
    date_to:      Optional[str] = Query(None),
    compare_from: Optional[str] = Query(None),
    compare_to:   Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    curr_start, curr_end, prev_start, prev_end = period_dates(
        period, date_from, date_to, compare_from, compare_to
    )
    camp_q = select(Campaign).where(Campaign.account_id == account_id)
    if active_only:
        camp_q = camp_q.where(Campaign.is_active == True)
    campaigns_result = await db.execute(camp_q.order_by(Campaign.name))
    campaigns = campaigns_result.scalars().all()

    def make_stats_q(dt_from, dt_to):
        return (
            select(
                Campaign.id,
                func.sum(KeywordStat.spend).label("spend"),
                func.sum(KeywordStat.clicks).label("clicks"),
                func.sum(KeywordStat.impressions).label("impressions"),
                func.avg(KeywordStat.avg_position).label("avg_position"),
                func.avg(KeywordStat.avg_click_position).label("avg_click_position"),
                func.avg(KeywordStat.traffic_volume).label("traffic_volume"),
                func.avg(KeywordStat.bounce_rate).label("bounce_rate"),
                func.sum(KeywordStat.sessions).label("sessions"),
            )
            .join(AdGroup, AdGroup.campaign_id == Campaign.id)
            .join(Keyword, Keyword.ad_group_id == AdGroup.id)
            .join(KeywordStat, KeywordStat.keyword_id == Keyword.id)
            .where(
                Campaign.account_id == account_id,
                KeywordStat.date >= dt_from,
                KeywordStat.date <= dt_to,
            )
            .group_by(Campaign.id)
        )

    curr_q   = await db.execute(make_stats_q(curr_start, curr_end))
    curr_map = {r.id: r for r in curr_q}

    prev_q   = await db.execute(make_stats_q(prev_start, prev_end))
    prev_map = {r.id: r for r in prev_q}

    analysis_result = await db.execute(
        select(AnalysisResult)
        .where(AnalysisResult.account_id == account_id)
        .order_by(desc(AnalysisResult.created_at))
        .limit(1)
    )
    analysis = analysis_result.scalar_one_or_none()

    camp_by_kw: dict[int, int] = {}
    if analysis and analysis.problems:
        kw_ids_in_sigs = [p["keyword_id"] for p in analysis.problems if p.get("keyword_id")]
        if kw_ids_in_sigs:
            kw_camp_q = await db.execute(
                select(Keyword.id, AdGroup.campaign_id)
                .join(AdGroup, AdGroup.id == Keyword.ad_group_id)
                .where(Keyword.id.in_(kw_ids_in_sigs))
            )
            camp_by_kw = {r.id: r.campaign_id for r in kw_camp_q}

    signals_by_camp: dict[int, list] = {}
    if analysis and analysis.problems:
        for p in analysis.problems:
            kw_id          = p.get("keyword_id")
            camp_id_direct = p.get("entity_id") if p.get("entity_type") == "campaign" else None
            camp_id        = camp_id_direct or camp_by_kw.get(kw_id)
            if camp_id:
                signals_by_camp.setdefault(camp_id, []).append(p)

    result = []
    for c in campaigns:
        s  = curr_map.get(c.id)
        ps = prev_map.get(c.id)

        cl  = int(s.clicks or 0)      if s else 0
        im  = int(s.impressions or 0) if s else 0
        sp  = float(s.spend or 0)     if s else 0
        pos = round(float(s.avg_position), 2)       if s and s.avg_position else None
        cpos = round(float(s.avg_click_position), 2) if s and s.avg_click_position else None
        traf = round(float(s.traffic_volume))         if s and s.traffic_volume else None
        br   = round(float(s.bounce_rate), 1)         if s and s.bounce_rate else None
        sess = int(s.sessions or 0)   if s else None

        pcl  = int(ps.clicks or 0)    if ps else 0
        pim  = int(ps.impressions or 0) if ps else 0
        psp  = float(ps.spend or 0)   if ps else 0

        cpc     = round(sp / cl, 2)    if cl > 0 else None
        ctr     = round(cl / im * 100, 2) if im > 0 else None
        p_cpc   = round(psp / pcl, 2) if pcl > 0 else None
        p_ctr   = round(pcl / pim * 100, 2) if pim > 0 else None
        p_pos   = float(ps.avg_position) if ps and ps.avg_position else None

        def d(curr_v, prev_v, invert=False):
            delta = calc_delta(curr_v, prev_v)
            if delta is None:
                return None
            return {"value": delta, "is_good": (delta < 0 if invert else delta > 0)}

        camp_signals    = signals_by_camp.get(c.id, [])
        has_epk         = any(p.get("type") == "epk_bid_collapse" for p in camp_signals)
        signals_critical = sum(1 for sig in camp_signals if sig.get("severity") == "critical")

        result.append({
            "id":           c.id,
            "direct_id":    c.direct_id,
            "name":         c.name,
            "campaign_type": c.campaign_type,
            "strategy_type": c.strategy_type,
            "status":       c.status,
            "is_active":    c.is_active,
            "spend":        round(sp, 2),
            "clicks":       cl,
            "impressions":  im,
            "avg_cpc":      cpc,
            "ctr":          ctr,
            "avg_position": pos,
            "avg_click_position": cpos,
            "traffic_volume": traf,
            "bounce_rate":  br,
            "sessions":     sess,
            "prev_spend":       round(psp, 2),
            "prev_clicks":      pcl,
            "prev_impressions": pim,
            "prev_avg_cpc":     p_cpc,
            "prev_ctr":         p_ctr,
            "prev_avg_position": round(p_pos, 2) if p_pos else None,
            "delta_spend":       d(sp,  psp, invert=True),
            "delta_clicks":      d(cl,  pcl),
            "delta_impressions": d(im,  pim),
            "delta_cpc":         d(cpc, p_cpc, invert=True),
            "delta_ctr":         d(ctr, p_ctr),
            "delta_position":    d(pos, p_pos, invert=True),
            "signals_count":    len(camp_signals),
            "signals_critical": signals_critical,
            "signals_warning":  sum(1 for sig in camp_signals if sig.get("severity") == "warning"),
            "has_epk_collapse": has_epk,
            "top_signal":       camp_signals[0] if camp_signals else None,
        })

    result.sort(key=lambda x: (
        0 if x["signals_critical"] > 0 else (1 if x["signals_count"] > 0 else 2),
        -x["spend"]
    ))
    return result


# ─── Ad Groups ──────────────────────────

@router.get("/accounts/{account_id}/ad-groups")
async def get_ad_groups(
    account_id: int,
    campaign_id: Optional[int] = None,
    period: str = Query("week"),
    db: AsyncSession = Depends(get_db),
):
    curr_start, curr_end, _, _ = period_dates(period)
    q = select(AdGroup).where(AdGroup.account_id == account_id)
    if campaign_id:
        q = q.where(AdGroup.campaign_id == campaign_id)
    result = await db.execute(q.order_by(AdGroup.name))
    groups = result.scalars().all()
    group_ids = [g.id for g in groups]
    if not group_ids:
        return []

    stats_q = await db.execute(
        select(
            Keyword.ad_group_id,
            func.sum(KeywordStat.spend).label("spend"),
            func.sum(KeywordStat.clicks).label("clicks"),
            func.sum(KeywordStat.impressions).label("impressions"),
            func.count(Keyword.id).label("kw_count"),
        )
        .join(KeywordStat, KeywordStat.keyword_id == Keyword.id)
        .where(
            Keyword.ad_group_id.in_(group_ids),
            KeywordStat.date >= curr_start,
            KeywordStat.date <= curr_end,
        )
        .group_by(Keyword.ad_group_id)
    )
    stats_map = {r.ad_group_id: r for r in stats_q}

    kw_count_q = await db.execute(
        select(Keyword.ad_group_id, func.count(Keyword.id).label("cnt"))
        .where(Keyword.ad_group_id.in_(group_ids))
        .group_by(Keyword.ad_group_id)
    )
    kw_map = {r.ad_group_id: r.cnt for r in kw_count_q}

    return [{
        "id":             g.id,
        "name":           g.name,
        "campaign_id":    g.campaign_id,
        "status":         g.status,
        "keywords_count": kw_map.get(g.id, 0),
        "spend":    round(float(stats_map[g.id].spend or 0), 2) if g.id in stats_map else 0,
        "clicks":   int(stats_map[g.id].clicks or 0) if g.id in stats_map else 0,
        "impressions": int(stats_map[g.id].impressions or 0) if g.id in stats_map else 0,
    } for g in groups]
