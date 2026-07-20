"""Конструктор отчьтов.

Выделено из монолитного routes.py без изменения логики.
"""
from datetime import timedelta
from typing import Optional
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, and_, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.models import (
    Campaign, AdGroup, Keyword, KeywordStat, Lead,
)
from app.api.routes._common import period_dates

router = APIRouter()
logger = logging.getLogger(__name__)


# ─── Конструктор отчьтов (мастер отчьтов) ────────────
#
# v1.7.0 — аналог "Мастера отчьтов" Директа / отчьтов Roistat: одна и та же
# статистика (KeywordStat + CRM), группируемая по кампании / группе /
# ключу / дню, без похода на 4 разные страницы. Столбцы отдаются все
# сразу единым плоским набором — какие из них показывать и в какой
# группировке решает фронтенд (settings-модалка на странице /reports), это не
# требует лишних запросов и держит бэкенд простым.

REPORT_GROUP_DIMENSIONS = {"campaign", "ad_group", "keyword", "date"}


@router.get("/accounts/{account_id}/report")
async def get_report(
    account_id: int,
    group_by: str = Query("campaign"),
    period: str = Query("month"),
    date_from: Optional[str] = Query(None),
    date_to:   Optional[str] = Query(None),
    campaign_id: Optional[int] = None,
    ad_group_id: Optional[int] = None,
    active_only: bool = Query(False),
    limit: int = Query(1000, le=5000),
    db: AsyncSession = Depends(get_db),
):
    if group_by not in REPORT_GROUP_DIMENSIONS:
        raise HTTPException(400, f"group_by должен быть один из {sorted(REPORT_GROUP_DIMENSIONS)}")

    curr_start, curr_end, _, _ = period_dates(period, date_from, date_to)

    conditions = [
        KeywordStat.account_id == account_id,
        KeywordStat.date >= curr_start,
        KeywordStat.date <= curr_end,
    ]
    query = (
        select(
            Campaign.id.label("campaign_id"), Campaign.name.label("campaign_name"),
            Campaign.strategy_type.label("strategy_type"), Campaign.is_active.label("campaign_is_active"),
            AdGroup.id.label("ad_group_id"), AdGroup.name.label("ad_group_name"),
            Keyword.id.label("keyword_id"), Keyword.phrase.label("keyword_phrase"),
            Keyword.current_bid.label("current_bid"),
            KeywordStat.date, KeywordStat.impressions, KeywordStat.clicks, KeywordStat.spend,
            KeywordStat.avg_position, KeywordStat.avg_click_position, KeywordStat.traffic_volume,
            KeywordStat.bounce_rate, KeywordStat.sessions, KeywordStat.weighted_ctr,
            KeywordStat.weighted_impressions, KeywordStat.avg_bid,
        )
        .select_from(KeywordStat)
        .join(Keyword, Keyword.id == KeywordStat.keyword_id)
        .join(AdGroup, AdGroup.id == Keyword.ad_group_id)
        .join(Campaign, Campaign.id == AdGroup.campaign_id)
        .where(and_(*conditions))
    )
    if campaign_id:
        query = query.where(Campaign.id == campaign_id)
    if ad_group_id:
        query = query.where(AdGroup.id == ad_group_id)
    if active_only:
        query = query.where(Campaign.is_active == True)

    result = await db.execute(query)
    raw = result.all()

    # ── Лиды/MQL/SQL по ключу за тот же период — присоединяются в Python,
    #    чтобы не городить динамический SQL GROUP BY под 4 разных измерения.
    leads_q = await db.execute(
        select(
            Lead.keyword_id,
            func.count(Lead.id).label("leads"),
            func.sum(case((Lead.is_mql.is_(True), 1), else_=0)).label("mql"),
            func.sum(case((Lead.is_sql.is_(True), 1), else_=0)).label("sql"),
        )
        .where(and_(
            Lead.account_id == account_id,
            Lead.keyword_id.isnot(None),
            Lead.created_at >= curr_start,
            Lead.created_at <= curr_end,
        ))
        .group_by(Lead.keyword_id)
    )
    leads_by_kw = {r.keyword_id: r for r in leads_q.all()}

    def dim_key(row):
        if group_by == "campaign":
            return ("campaign", row.campaign_id)
        if group_by == "ad_group":
            return ("ad_group", row.ad_group_id)
        if group_by == "keyword":
            return ("keyword", row.keyword_id)
        return ("date", row.date.strftime("%Y-%m-%d"))

    groups: dict = {}
    for row in raw:
        key = dim_key(row)
        g = groups.setdefault(key, {
            "campaign_id": row.campaign_id, "campaign_name": row.campaign_name,
            "strategy_type": row.strategy_type, "campaign_is_active": row.campaign_is_active,
            "ad_group_id": row.ad_group_id, "ad_group_name": row.ad_group_name,
            "keyword_id": row.keyword_id, "keyword_phrase": row.keyword_phrase,
            "current_bid": float(row.current_bid) if row.current_bid else None,
            "date": row.date.strftime("%Y-%m-%d") if group_by == "date" else None,
            "impressions": 0, "clicks": 0, "spend": 0.0,
            "_pos_sum": 0.0, "_cpos_sum": 0.0, "_tv_sum": 0.0, "_wctr_sum": 0.0,
            "_bounce_sum": 0.0, "_bounce_n": 0, "_n": 0,
            "sessions": 0, "weighted_impressions": 0,
            "_kw_ids": set(),
        })
        g["impressions"] += int(row.impressions or 0)
        g["clicks"]      += int(row.clicks or 0)
        g["spend"]       += float(row.spend or 0)
        g["_pos_sum"]    += float(row.avg_position or 0)
        g["_cpos_sum"]   += float(row.avg_click_position or 0)
        g["_tv_sum"]     += float(row.traffic_volume or 0)
        g["_wctr_sum"]   += float(row.weighted_ctr or 0)
        g["sessions"]    += int(row.sessions or 0)
        g["weighted_impressions"] += int(row.weighted_impressions or 0)
        if row.bounce_rate:
            g["_bounce_sum"] += float(row.bounce_rate)
            g["_bounce_n"]   += 1
        g["_n"] += 1
        if row.keyword_id:
            g["_kw_ids"].add(row.keyword_id)

    rows_out = []
    totals = {"impressions": 0, "clicks": 0, "spend": 0.0, "leads": 0, "mql": 0, "sql": 0}
    for key, g in groups.items():
        n = g["_n"] or 1
        clicks = g["clicks"]
        impressions = g["impressions"]
        spend = g["spend"]
        leads = mql = sql = 0
        for kw_id in g["_kw_ids"]:
            r = leads_by_kw.get(kw_id)
            if r:
                leads += int(r.leads or 0)
                mql   += int(r.mql or 0)
                sql   += int(r.sql or 0)

        row_out = {
            "group_by":            group_by,
            "campaign_id":         g["campaign_id"],
            "campaign_name":       g["campaign_name"],
            "strategy_type":       g["strategy_type"],
            "ad_group_id":         g["ad_group_id"] if group_by in ("ad_group", "keyword") else None,
            "ad_group_name":       g["ad_group_name"] if group_by in ("ad_group", "keyword") else None,
            "keyword_id":          g["keyword_id"] if group_by == "keyword" else None,
            "keyword_phrase":      g["keyword_phrase"] if group_by == "keyword" else None,
            "current_bid":         g["current_bid"] if group_by == "keyword" else None,
            "date":                g["date"],
            "impressions":         impressions,
            "clicks":              clicks,
            "spend":               round(spend, 2),
            "ctr":                 round(clicks / impressions * 100, 2) if impressions else None,
            "avg_cpc":             round(spend / clicks, 2) if clicks else None,
            "avg_position":        round(g["_pos_sum"] / n, 2) if g["_pos_sum"] else None,
            "avg_click_position":  round(g["_cpos_sum"] / n, 2) if g["_cpos_sum"] else None,
            "traffic_volume":      round(g["_tv_sum"] / n, 1) if g["_tv_sum"] else None,
            "weighted_ctr":        round(g["_wctr_sum"] / n, 2) if g["_wctr_sum"] else None,
            "weighted_impressions": g["weighted_impressions"] or None,
            "bounce_rate":         round(g["_bounce_sum"] / g["_bounce_n"], 1) if g["_bounce_n"] else None,
            "sessions":            g["sessions"] or None,
            "leads":               leads,
            "mql":                 mql,
            "sql":                 sql,
            "cr_lead_mql":         round(mql / leads * 100, 1) if leads else None,
            "cr_mql_sql":          round(sql / mql * 100, 1) if mql else None,
            "cpl":                 round(spend / leads, 2) if leads else None,
            "cost_per_mql":        round(spend / mql, 2) if mql else None,
            "cost_per_sql":        round(spend / sql, 2) if sql else None,
        }
        rows_out.append(row_out)
        totals["impressions"] += impressions
        totals["clicks"]      += clicks
        totals["spend"]       += spend
        totals["leads"]       += leads
        totals["mql"]         += mql
        totals["sql"]         += sql

    sort_key = "date" if group_by == "date" else "spend"
    rows_out.sort(key=lambda r: r.get(sort_key) or 0, reverse=(group_by != "date"))
    rows_out = rows_out[:limit]

    totals["ctr"] = round(totals["clicks"] / totals["impressions"] * 100, 2) if totals["impressions"] else None
    totals["avg_cpc"] = round(totals["spend"] / totals["clicks"], 2) if totals["clicks"] else None
    totals["cpl"] = round(totals["spend"] / totals["leads"], 2) if totals["leads"] else None
    totals["cost_per_sql"] = round(totals["spend"] / totals["sql"], 2) if totals["sql"] else None
    totals["spend"] = round(totals["spend"], 2)

    return {
        "group_by": group_by,
        "period": period,
        "period_dates": {
            "from": curr_start.date().isoformat(),
            "to":   (curr_end - timedelta(days=1)).date().isoformat(),
        },
        "rows": rows_out,
        "totals": totals,
        "row_count": len(rows_out),
    }
