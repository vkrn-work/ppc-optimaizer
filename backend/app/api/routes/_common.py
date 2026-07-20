"""Общие импорты и вспомогательные функции для API-роутов.

Выделено из монолитного routes.py без изменения логики.
"""
from datetime import datetime, timedelta
from typing import Optional
import logging

from sqlalchemy import select, func, and_, case

from app.models.models import (
    AdGroup, Keyword, KeywordStat, Lead,
)

logger = logging.getLogger(__name__)


# ─── Helpers ──────────────────────────────────

def period_dates(period: str, date_from: Optional[str] = None, date_to: Optional[str] = None,
                 compare_from: Optional[str] = None, compare_to: Optional[str] = None):
    """
    Возвращает (curr_start, curr_end, prev_start, prev_end).
    Приоритет: явные даты > preset period.
    """
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    if date_from and date_to:
        try:
            curr_start = datetime.strptime(date_from, "%Y-%m-%d")
            curr_end   = datetime.strptime(date_to,   "%Y-%m-%d") + timedelta(days=1)
            if compare_from and compare_to:
                prev_start = datetime.strptime(compare_from, "%Y-%m-%d")
                prev_end   = datetime.strptime(compare_to,   "%Y-%m-%d") + timedelta(days=1)
            else:
                delta = (curr_end - curr_start)
                prev_end   = curr_start
                prev_start = prev_end - delta
            return curr_start, curr_end, prev_start, prev_end
        except ValueError:
            pass

    if period == "yesterday":
        curr_end   = today
        curr_start = today - timedelta(days=1)
        prev_end   = curr_start
        prev_start = prev_end - timedelta(days=14)
    elif period == "3d":
        curr_end   = today
        curr_start = today - timedelta(days=3)
        prev_end   = curr_start
        prev_start = prev_end - timedelta(days=3)
    elif period == "month":
        curr_end   = today
        curr_start = today - timedelta(days=30)
        prev_end   = curr_start
        prev_start = prev_end - timedelta(days=30)
    else:  # week (default)
        curr_end   = today
        curr_start = today - timedelta(days=7)
        prev_end   = curr_start
        prev_start = prev_end - timedelta(days=7)
    return curr_start, curr_end, prev_start, prev_end


async def agg_kw_stats(db, account_id: int, date_from: datetime, date_to: datetime,
                       campaign_id: Optional[int] = None) -> dict:
    conditions = [
        KeywordStat.account_id == account_id,
        KeywordStat.date >= date_from,
        KeywordStat.date <= date_to,
    ]
    if campaign_id:
        conditions.append(
            KeywordStat.keyword_id.in_(
                select(Keyword.id)
                .join(AdGroup, AdGroup.id == Keyword.ad_group_id)
                .where(AdGroup.campaign_id == campaign_id)
            )
        )
    q = select(
        func.sum(KeywordStat.clicks).label("clicks"),
        func.sum(KeywordStat.impressions).label("impressions"),
        func.sum(KeywordStat.spend).label("spend"),
        func.avg(KeywordStat.avg_position).label("avg_position"),
        func.avg(KeywordStat.avg_click_position).label("avg_click_position"),
        func.avg(KeywordStat.traffic_volume).label("avg_traffic_volume"),
        func.avg(KeywordStat.bounce_rate).label("bounce_rate"),
        func.sum(KeywordStat.sessions).label("sessions"),
        func.avg(KeywordStat.weighted_ctr).label("weighted_ctr"),
    ).where(and_(*conditions))
    r = await db.execute(q)
    row = r.one()
    clicks      = int(row.clicks or 0)
    impressions = int(row.impressions or 0)
    spend       = float(row.spend or 0)
    avg_cpc = round(spend / clicks, 2) if clicks > 0 else None
    ctr     = round(clicks / impressions * 100, 2) if impressions > 0 else None
    return {
        "clicks":               clicks,
        "impressions":          impressions,
        "spend":                round(spend, 2),
        "avg_position":         round(float(row.avg_position), 2) if row.avg_position else None,
        "avg_click_position":   round(float(row.avg_click_position), 2) if row.avg_click_position else None,
        "avg_cpc":              avg_cpc,
        "avg_traffic_volume":   round(float(row.avg_traffic_volume)) if row.avg_traffic_volume else None,
        "ctr":                  ctr,
        "bounce_rate":          round(float(row.bounce_rate), 1) if row.bounce_rate else None,
        "sessions":             int(row.sessions or 0) if row.sessions else None,
        "weighted_ctr":         round(float(row.weighted_ctr), 2) if row.weighted_ctr else None,
    }


def calc_delta(curr, prev, invert=False):
    if not prev or prev == 0 or curr is None:
        return None
    d = (curr - prev) / abs(prev) * 100
    return round(-d if invert else d, 1)


async def agg_leads(db, account_id: int, date_from: datetime, date_to: datetime, spend: float) -> dict:
    """
    Агрегация CRM-воронки (Lead) за период — leads/MQL/SQL + CPL/cost_per_mql/cost_per_sql.
    Данные уже есть в БД через /crm-import (см. app/importers/crm_importer.py), но
    раньше не попадали на дашборд — там был статичный плейсхолдер "CRM не подключена"
    независимо от того, загружали ли выгрузку. spend берётся из уже посчитанного
    agg_kw_stats() того же периода, чтобы не пересчитывать сумму расхода дважды.
    """
    q = select(
        func.count(Lead.id).label("leads"),
        func.sum(case((Lead.is_mql.is_(True), 1), else_=0)).label("mql"),
        func.sum(case((Lead.is_sql.is_(True), 1), else_=0)).label("sql"),
    ).where(and_(
        Lead.account_id == account_id,
        Lead.created_at >= date_from,
        Lead.created_at <= date_to,
    ))
    r = await db.execute(q)
    row = r.one()
    leads = int(row.leads or 0)
    mql   = int(row.mql or 0)
    sql   = int(row.sql or 0)
    return {
        "leads": leads,
        "mql":   mql,
        "sql":   sql,
        "cr_lead_mql":   round(mql / leads * 100, 1) if leads else None,
        "cr_mql_sql":    round(sql / mql * 100, 1) if mql else None,
        "cpl":           round(spend / leads, 2) if leads else None,
        "cost_per_mql":  round(spend / mql, 2) if mql else None,
        "cost_per_sql":  round(spend / sql, 2) if sql else None,
    }


def mk_crm_kpi_block(curr: dict, prev: dict) -> dict:
    def mk_delta(key, invert=False):
        d = calc_delta(curr.get(key), prev.get(key))
        if d is None:
            return None
        return {"value": d, "is_good": (d < 0 if invert else d > 0)}

    return {
        "leads":         {"value": curr["leads"],         "delta": mk_delta("leads"),                 "prev": prev["leads"]},
        "mql":           {"value": curr["mql"],            "delta": mk_delta("mql"),                   "prev": prev["mql"]},
        "sql":           {"value": curr["sql"],            "delta": mk_delta("sql"),                   "prev": prev["sql"]},
        "cr_lead_mql":   {"value": curr["cr_lead_mql"],     "delta": mk_delta("cr_lead_mql"),           "prev": prev["cr_lead_mql"]},
        "cr_mql_sql":    {"value": curr["cr_mql_sql"],      "delta": mk_delta("cr_mql_sql"),            "prev": prev["cr_mql_sql"]},
        "cpl":           {"value": curr["cpl"],             "delta": mk_delta("cpl", invert=True),      "prev": prev["cpl"]},
        "cost_per_mql":  {"value": curr["cost_per_mql"],    "delta": mk_delta("cost_per_mql", invert=True), "prev": prev["cost_per_mql"]},
        "cost_per_sql":  {"value": curr["cost_per_sql"],    "delta": mk_delta("cost_per_sql", invert=True), "prev": prev["cost_per_sql"]},
    }


def mk_kpi_block(curr_kpi: dict, prev_kpi: dict) -> dict:
    def mk_delta(key, invert=False):
        d = calc_delta(curr_kpi.get(key), prev_kpi.get(key))
        if d is None:
            return None
        return {"value": d, "is_good": (d < 0 if invert else d > 0)}

    return {
        "clicks":             {"value": curr_kpi["clicks"],             "delta": mk_delta("clicks"),             "prev": prev_kpi["clicks"]},
        "impressions":        {"value": curr_kpi["impressions"],        "delta": mk_delta("impressions"),        "prev": prev_kpi["impressions"]},
        "spend":              {"value": curr_kpi["spend"],              "delta": mk_delta("spend", invert=True),  "prev": prev_kpi["spend"]},
        "ctr":                {"value": curr_kpi["ctr"],                "delta": mk_delta("ctr"),                "prev": prev_kpi["ctr"]},
        "avg_cpc":            {"value": curr_kpi["avg_cpc"],            "delta": mk_delta("avg_cpc", invert=True), "prev": prev_kpi["avg_cpc"]},
        "avg_position":       {"value": curr_kpi["avg_position"],       "delta": mk_delta("avg_position", invert=True),       "prev": prev_kpi["avg_position"]},
        "avg_click_position": {"value": curr_kpi["avg_click_position"], "delta": mk_delta("avg_click_position", invert=True), "prev": prev_kpi["avg_click_position"]},
        "avg_traffic_volume": {"value": curr_kpi["avg_traffic_volume"], "delta": mk_delta("avg_traffic_volume"),               "prev": prev_kpi["avg_traffic_volume"]},
        "bounce_rate":        {"value": curr_kpi.get("bounce_rate"),  "delta": mk_delta("bounce_rate", invert=True), "prev": prev_kpi.get("bounce_rate")},
        "sessions":           {"value": curr_kpi.get("sessions"),     "delta": mk_delta("sessions"),                "prev": prev_kpi.get("sessions")},
        "weighted_ctr":       {"value": curr_kpi.get("weighted_ctr"), "delta": mk_delta("weighted_ctr"),             "prev": prev_kpi.get("weighted_ctr")},
    }


async def get_daily_series(db, account_id: int, date_from: datetime, date_to: datetime,
                            campaign_id: Optional[int] = None) -> list:
    conditions = [
        KeywordStat.account_id == account_id,
        KeywordStat.date >= date_from,
        KeywordStat.date <= date_to,
    ]
    if campaign_id:
        conditions.append(
            KeywordStat.keyword_id.in_(
                select(Keyword.id)
                .join(AdGroup, AdGroup.id == Keyword.ad_group_id)
                .where(AdGroup.campaign_id == campaign_id)
            )
        )
    q = await db.execute(
        select(
            KeywordStat.date,
            func.sum(KeywordStat.clicks).label("clicks"),
            func.sum(KeywordStat.impressions).label("impressions"),
            func.sum(KeywordStat.spend).label("spend"),
            func.avg(KeywordStat.avg_position).label("avg_position"),
            func.avg(KeywordStat.avg_click_position).label("avg_click_position"),
            func.avg(KeywordStat.traffic_volume).label("traffic_volume"),
        )
        .where(and_(*conditions))
        .group_by(KeywordStat.date)
        .order_by(KeywordStat.date)
    )
    rows = []
    for r in q:
        cl = int(r.clicks or 0)
        im = int(r.impressions or 0)
        sp = float(r.spend or 0)
        rows.append({
            "date": r.date.strftime("%Y-%m-%d"),
            "clicks": cl,
            "impressions": im,
            "spend": round(sp, 2),
            "avg_cpc": round(sp / cl, 2) if cl > 0 else None,
            "ctr": round(cl / im * 100, 2) if im > 0 else None,
            "avg_position": round(float(r.avg_position), 2) if r.avg_position else None,
            "avg_click_position": round(float(r.avg_click_position), 2) if r.avg_click_position else None,
            "traffic_volume": round(float(r.traffic_volume)) if r.traffic_volume else None,
        })
    return rows
