"""Конструктор отчётов.

v1.7.0 — аналог «Мастера отчётов» Директа / отчётов Roistat: одна и та же
статистика (KeywordStat + CRM), группируемая по кампании / группе / ключу /
дню. Столбцы отдаются все сразу плоским набором — что показывать решает фронт.

v1.7.5 — КРИТИЧЕСКИЙ ФИКС ПОДСЧЁТА ЗАЯВОК.

На боевом кабинете отчёт «По кампаниям» за месяц показывал 1 заявку на
64 269 ₽ расхода при семи активных кампаниях — шесть из семи строк были
с нулями. Причина была ровно здесь:

    .where(Lead.keyword_id.isnot(None))
    .group_by(Lead.keyword_id)

Запасной уровень атрибуции (leads.campaign_id, добавленный в v1.7.4 именно
затем, чтобы заявки не терялись) в отчёте не участвовал ВООБЩЕ. Заявка,
привязанная к кампании, но не к ключу, показывалась как ноль — а таких
большинство. При этом dashboard.py считает ВСЕ заявки периода, то есть
два экрана одного продукта показывали разную воронку и не сходились друг с
другом ни при каких данных.

Теперь агрегация заявок зависит от разреза:
    campaign — coalesce(leads.campaign_id, keyword → ad_group → campaign);
    ad_group — через ключ (у заявки нет своего ad_group_id);
    keyword  — по keyword_id, как раньше;
    date     — по дате заявки.

Второй фикс той же природы: отчёт строился ОТ KeywordStat, поэтому всё, у
чего нет открутки в периоде, не попадало в выборку вместе со своими
заявками (заявка в день без показов, остановленная кампания). Такие строки
теперь достраиваются с нулевым расходом и флагом no_spend_in_period.

И главное: в ответ добавлен блок attribution с нераспределённым остатком.
Неразнесённые заявки больше не растворяются в нулях по строкам — видно,
сколько их и что итог отчёта не равен итогу CRM.
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

REPORT_GROUP_DIMENSIONS = {"campaign", "ad_group", "keyword", "date"}

_LEAD_AGG = (
    func.count(Lead.id).label("leads"),
    func.sum(case((Lead.is_mql.is_(True), 1), else_=0)).label("mql"),
    func.sum(case((Lead.is_sql.is_(True), 1), else_=0)).label("sql"),
)


async def _leads_by_dimension(db: AsyncSession, account_id: int, group_by: str,
                              curr_start, curr_end) -> dict:
    """Заявки за период, сгруппированные по тому же измерению, что и отчёт.

    Возвращает {ключ измерения: {"leads": n, "mql": n, "sql": n}}.

    Ключевой момент для разреза campaign: заявка участвует, если у неё есть
    ЛИБО свой campaign_id, ЛИБО ключ, через который кампания выводится. До
    v1.7.5 учитывался только второй случай, и отчёт показывал нули.
    """
    base_where = [
        Lead.account_id == account_id,
        Lead.created_at >= curr_start,
        Lead.created_at <= curr_end,
    ]

    if group_by == "campaign":
        dim = func.coalesce(Lead.campaign_id, AdGroup.campaign_id)
        q = (
            select(dim.label("dim"), *_LEAD_AGG)
            .select_from(Lead)
            .outerjoin(Keyword, Lead.keyword_id == Keyword.id)
            .outerjoin(AdGroup, Keyword.ad_group_id == AdGroup.id)
            .where(and_(*base_where, dim.isnot(None)))
            .group_by(dim)
        )
    elif group_by == "ad_group":
        # У заявки нет собственного ad_group_id — только через ключ.
        # Заявки уровня кампании сюда не попадают и честно уйдут в остаток.
        q = (
            select(Keyword.ad_group_id.label("dim"), *_LEAD_AGG)
            .select_from(Lead)
            .join(Keyword, Lead.keyword_id == Keyword.id)
            .where(and_(*base_where, Keyword.ad_group_id.isnot(None)))
            .group_by(Keyword.ad_group_id)
        )
    elif group_by == "keyword":
        q = (
            select(Lead.keyword_id.label("dim"), *_LEAD_AGG)
            .where(and_(*base_where, Lead.keyword_id.isnot(None)))
            .group_by(Lead.keyword_id)
        )
    else:  # date
        dim = func.date(Lead.created_at)
        q = (
            select(dim.label("dim"), *_LEAD_AGG)
            .where(and_(*base_where))
            .group_by(dim)
        )

    rows = (await db.execute(q)).all()
    out = {}
    for r in rows:
        key = r.dim
        if group_by == "date":
            key = key.isoformat() if hasattr(key, "isoformat") else str(key)[:10]
        out[key] = {"leads": int(r.leads or 0), "mql": int(r.mql or 0), "sql": int(r.sql or 0)}
    return out


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
        query = query.where(Campaign.is_active == True)  # noqa: E712

    raw = (await db.execute(query)).all()

    # ── Заявки в том же разрезе, что и отчёт (v1.7.5).
    leads_by_dim = await _leads_by_dimension(db, account_id, group_by, curr_start, curr_end)

    # Все заявки периода — тот же счётчик, что на дашборде. Нужен, чтобы
    # показать нераспределённый остаток, а не делать вид, что его нет.
    crm_row = (await db.execute(
        select(*_LEAD_AGG).where(and_(
            Lead.account_id == account_id,
            Lead.created_at >= curr_start,
            Lead.created_at <= curr_end,
        ))
    )).one()
    crm_totals = {
        "leads": int(crm_row.leads or 0),
        "mql": int(crm_row.mql or 0),
        "sql": int(crm_row.sql or 0),
    }

    filtered_dims = bool(campaign_id or ad_group_id or active_only)

    # Имена для строк, которых нет в статистике периода (см. ниже).
    campaign_names: dict = {}
    ad_group_names: dict = {}
    keyword_phrases: dict = {}
    if not filtered_dims and leads_by_dim:
        ids = [k for k in leads_by_dim if isinstance(k, int)]
        if ids and group_by == "campaign":
            campaign_names = dict((await db.execute(
                select(Campaign.id, Campaign.name).where(Campaign.id.in_(ids))
            )).all())
        elif ids and group_by == "ad_group":
            ad_group_names = dict((await db.execute(
                select(AdGroup.id, AdGroup.name).where(AdGroup.id.in_(ids))
            )).all())
        elif ids and group_by == "keyword":
            keyword_phrases = dict((await db.execute(
                select(Keyword.id, Keyword.phrase).where(Keyword.id.in_(ids))
            )).all())

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

    # ── v1.7.5: строки, где есть ЗАЯВКИ, но нет открутки в периоде.
    #    Без этого заявка, пришедшая в день без показов (или по остановленной
    #    кампании), просто исчезала из отчёта — тот же класс ошибки, что и
    #    счёт только по keyword_id, только по другой оси.
    if not filtered_dims:
        known = {k[1] for k in groups}
        for dim_value, stat in leads_by_dim.items():
            if dim_value in known or not stat.get("leads"):
                continue
            groups[(group_by, dim_value)] = {
                "campaign_id": dim_value if group_by == "campaign" else None,
                "campaign_name": campaign_names.get(dim_value) if group_by == "campaign" else None,
                "strategy_type": None, "campaign_is_active": None,
                "ad_group_id": dim_value if group_by == "ad_group" else None,
                "ad_group_name": ad_group_names.get(dim_value) if group_by == "ad_group" else None,
                "keyword_id": dim_value if group_by == "keyword" else None,
                "keyword_phrase": keyword_phrases.get(dim_value) if group_by == "keyword" else None,
                "current_bid": None,
                "date": dim_value if group_by == "date" else None,
                "impressions": 0, "clicks": 0, "spend": 0.0,
                "_pos_sum": 0.0, "_cpos_sum": 0.0, "_tv_sum": 0.0, "_wctr_sum": 0.0,
                "_bounce_sum": 0.0, "_bounce_n": 0, "_n": 0,
                "sessions": 0, "weighted_impressions": 0,
                "no_spend_in_period": True,
            }

    rows_out = []
    totals = {"impressions": 0, "clicks": 0, "spend": 0.0, "leads": 0, "mql": 0, "sql": 0}
    for key, g in groups.items():
        n = g["_n"] or 1
        clicks = g["clicks"]
        impressions = g["impressions"]
        spend = g["spend"]

        # v1.7.5: берём агрегат по тому же измерению, а не сумму по ключам группы.
        lead_stat = leads_by_dim.get(key[1], {})
        leads = lead_stat.get("leads", 0)
        mql   = lead_stat.get("mql", 0)
        sql   = lead_stat.get("sql", 0)

        rows_out.append({
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
            "no_spend_in_period":  g.get("no_spend_in_period", False),
            "leads":               leads,
            "mql":                 mql,
            "sql":                 sql,
            "cr_lead_mql":         round(mql / leads * 100, 1) if leads else None,
            "cr_mql_sql":          round(sql / mql * 100, 1) if mql else None,
            "cpl":                 round(spend / leads, 2) if leads else None,
            "cost_per_mql":        round(spend / mql, 2) if mql else None,
            "cost_per_sql":        round(spend / sql, 2) if sql else None,
        })
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

    # ── Нераспределённый остаток (v1.7.5).
    #
    #    При фильтрах остаток не считаем: часть заявок относится к
    #    отфильтрованным кампаниям, и сравнивать с общим итогом CRM
    #    некорректно — честнее сказать, что не считали, чем показать число,
    #    которое выглядит как потерянные заявки.
    filtered = filtered_dims
    attribution = {
        "leads_total_crm": crm_totals["leads"],
        "sql_total_crm": crm_totals["sql"],
        "leads_in_report": totals["leads"],
        "sql_in_report": totals["sql"],
        "filtered": filtered,
        "leads_unattributed": None if filtered else crm_totals["leads"] - totals["leads"],
        "sql_unattributed": None if filtered else crm_totals["sql"] - totals["sql"],
        "note": (
            "leads_total_crm — все заявки периода (тот же счётчик, что на дашборде). "
            "leads_unattributed — сколько из них не разнеслось по текущему разрезу. "
            "Если это число велико — запустите POST /accounts/{id}/leads/reattribute и "
            "посмотрите GET /accounts/{id}/leads/attribution. При активных фильтрах "
            "остаток не считается (filtered=true)."
        ),
    }

    return {
        "group_by": group_by,
        "period": period,
        "period_dates": {
            "from": curr_start.date().isoformat(),
            "to":   (curr_end - timedelta(days=1)).date().isoformat(),
        },
        "rows": rows_out,
        "totals": totals,
        "attribution": attribution,
        "row_count": len(rows_out),
    }
