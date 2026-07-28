"""Конструктор отчётов.

v1.7.0 — аналог «Мастера отчётов» Директа / отчётов Roistat.

v1.7.5 — заявки считаются в том же разрезе, что и отчёт (по кампании через
campaign_id, а не только по keyword_id), + блок attribution с остатком.

v1.7.6 — РСЯ И ВЛОЖЕННОСТЬ.

Две причины, по которым отчёт расходился с Роистатом:

  1. РСЯ/ретаргетинг был невидим ЦЕЛИКОМ. Отчёт строился только из
     keyword_stats, а у сетевых кампаний нет ключевых слов → ни расхода, ни
     заявок. На боевом кабинете это ~6000 ₽ расхода и 3 заявки за неделю,
     которых в отчёте просто не было (в Роистате они есть, канал «РСЯ»).
     Теперь расход на уровне кампаний берётся из campaign_stats
     (CAMPAIGN_PERFORMANCE_REPORT, покрывает ВСЕ типы кампаний), а заявки —
     по Lead.campaign_id, который теперь проставляется и для РСЯ.

  2. «Только активные» отключал достройку строк по заявкам. Заявка по
     кампании без открутки в периоде не показывалась. Теперь достройка идёт
     всегда, но под фильтром активных отсекается по Campaign.is_active.

Плюс уровень группы: у заявки теперь есть ad_group_id (см. lead_attribution),
поэтому разрез «По группам» по заявкам больше не пустой, и появился
вложенный endpoint /report/tree (кампания → группа → ключ).
"""
from datetime import timedelta
from typing import Optional
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, and_, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.models import (
    Campaign, AdGroup, Keyword, KeywordStat, CampaignStat, Lead,
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
    """Заявки за период в том же разрезе, что и отчёт.

    v1.7.6: используем прямые Lead.campaign_id / Lead.ad_group_id (теперь
    заполняются для всех уровней, включая РСЯ), с запасным выводом через ключ.
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
        dim = func.coalesce(Lead.ad_group_id, Keyword.ad_group_id)
        q = (
            select(dim.label("dim"), *_LEAD_AGG)
            .select_from(Lead)
            .outerjoin(Keyword, Lead.keyword_id == Keyword.id)
            .where(and_(*base_where, dim.isnot(None)))
            .group_by(dim)
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


async def _campaign_spend(db: AsyncSession, account_id: int, curr_start, curr_end) -> dict:
    """Расход/трафик по кампаниям из campaign_stats (покрывает РСЯ).

    Возвращает {campaign_id: {impressions, clicks, spend}}. Пусто, если сбор
    статистики кампаний ещё не прогонялся — тогда отчёт откатывается на
    расход из keyword_stats (search-only), как было до v1.7.6.
    """
    rows = (await db.execute(
        select(
            CampaignStat.campaign_id,
            func.sum(CampaignStat.impressions).label("impressions"),
            func.sum(CampaignStat.clicks).label("clicks"),
            func.sum(CampaignStat.spend).label("spend"),
        )
        .where(and_(
            CampaignStat.account_id == account_id,
            CampaignStat.date >= curr_start,
            CampaignStat.date <= curr_end,
        ))
        .group_by(CampaignStat.campaign_id)
    )).all()
    return {
        r.campaign_id: {
            "impressions": int(r.impressions or 0),
            "clicks": int(r.clicks or 0),
            "spend": float(r.spend or 0),
        }
        for r in rows
    }


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

    leads_by_dim = await _leads_by_dimension(db, account_id, group_by, curr_start, curr_end)
    camp_spend = await _campaign_spend(db, account_id, curr_start, curr_end)

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

    # ── v1.7.6: расход кампаний из campaign_stats (покрывает РСЯ). Если для
    #    кампании есть кампания-уровневый расход, он ЗАМЕЩАЕТ сумму по ключам
    #    (та неполна: не включает РСЯ и не-ключевой трафик).
    if group_by == "campaign" and camp_spend:
        for (dim_type, cid), g in groups.items():
            cs = camp_spend.get(cid)
            if cs:
                g["impressions"] = cs["impressions"]
                g["clicks"] = cs["clicks"]
                g["spend"] = cs["spend"]

    # ── Достройка строк, которых нет в keyword_stats (v1.7.5/1.7.6):
    #    - кампании с расходом РСЯ (есть в campaign_stats, нет ключей);
    #    - строки, где есть заявки, но нет открутки в периоде.
    #    Под фильтром активных РСЯ-кампании отсекаются по is_active.
    if group_by == "campaign":
        known = {k[1] for k in groups}
        extra_ids = set(camp_spend) | set(leads_by_dim)
        missing = [cid for cid in extra_ids if cid and cid not in known]
        if missing:
            camp_rows = (await db.execute(
                select(Campaign.id, Campaign.name, Campaign.strategy_type,
                       Campaign.is_active)
                .where(Campaign.id.in_(missing))
            )).all()
            for cid, name, strat, is_active in camp_rows:
                if active_only and not is_active:
                    continue
                if campaign_id and cid != campaign_id:
                    continue
                cs = camp_spend.get(cid, {})
                groups[("campaign", cid)] = {
                    "campaign_id": cid, "campaign_name": name,
                    "strategy_type": strat, "campaign_is_active": is_active,
                    "ad_group_id": None, "ad_group_name": None,
                    "keyword_id": None, "keyword_phrase": None, "current_bid": None,
                    "date": None,
                    "impressions": cs.get("impressions", 0),
                    "clicks": cs.get("clicks", 0),
                    "spend": cs.get("spend", 0.0),
                    "_pos_sum": 0.0, "_cpos_sum": 0.0, "_tv_sum": 0.0, "_wctr_sum": 0.0,
                    "_bounce_sum": 0.0, "_bounce_n": 0, "_n": 0,
                    "sessions": 0, "weighted_impressions": 0,
                    "no_keywords": True,
                    "no_spend_in_period": not cs,
                }
    elif not filtered_dims:
        # ad_group / keyword / date: достроить строки с заявками без открутки
        known = {k[1] for k in groups}
        names = await _dim_names(db, group_by, [k for k in leads_by_dim if k not in known])
        for dim_value, stat in leads_by_dim.items():
            if dim_value in known or not stat.get("leads"):
                continue
            groups[(group_by, dim_value)] = _empty_group(group_by, dim_value, names)

    rows_out = []
    totals = {"impressions": 0, "clicks": 0, "spend": 0.0, "leads": 0, "mql": 0, "sql": 0}
    for key, g in groups.items():
        n = g["_n"] or 1
        clicks = g["clicks"]; impressions = g["impressions"]; spend = g["spend"]

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
            "no_keywords":         g.get("no_keywords", False),
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

    filtered = filtered_dims
    attribution = {
        "leads_total_crm": crm_totals["leads"],
        "sql_total_crm": crm_totals["sql"],
        "leads_in_report": totals["leads"],
        "sql_in_report": totals["sql"],
        "filtered": filtered,
        "leads_unattributed": None if filtered else crm_totals["leads"] - totals["leads"],
        "sql_unattributed": None if filtered else crm_totals["sql"] - totals["sql"],
        "campaign_spend_source": "campaign_stats" if camp_spend else "keyword_stats_fallback",
        "note": (
            "leads_total_crm — все заявки периода (как на дашборде). "
            "leads_unattributed — сколько не разнеслось по текущему разрезу. "
            "campaign_spend_source=keyword_stats_fallback означает, что "
            "статистика кампаний (CAMPAIGN_PERFORMANCE_REPORT) ещё не собрана и "
            "расход РСЯ может быть занижен — нужен запуск синхронизации."
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


async def _dim_names(db: AsyncSession, group_by: str, ids: list) -> dict:
    ids = [i for i in ids if isinstance(i, int)]
    if not ids:
        return {}
    if group_by == "ad_group":
        return dict((await db.execute(
            select(AdGroup.id, AdGroup.name).where(AdGroup.id.in_(ids))
        )).all())
    if group_by == "keyword":
        return dict((await db.execute(
            select(Keyword.id, Keyword.phrase).where(Keyword.id.in_(ids))
        )).all())
    return {}


def _empty_group(group_by: str, dim_value, names: dict) -> dict:
    return {
        "campaign_id": None, "campaign_name": None,
        "strategy_type": None, "campaign_is_active": None,
        "ad_group_id": dim_value if group_by == "ad_group" else None,
        "ad_group_name": names.get(dim_value) if group_by == "ad_group" else None,
        "keyword_id": dim_value if group_by == "keyword" else None,
        "keyword_phrase": names.get(dim_value) if group_by == "keyword" else None,
        "current_bid": None,
        "date": dim_value if group_by == "date" else None,
        "impressions": 0, "clicks": 0, "spend": 0.0,
        "_pos_sum": 0.0, "_cpos_sum": 0.0, "_tv_sum": 0.0, "_wctr_sum": 0.0,
        "_bounce_sum": 0.0, "_bounce_n": 0, "_n": 0,
        "sessions": 0, "weighted_impressions": 0,
        "no_spend_in_period": True,
    }


# ─── Вложенный отчёт: кампания → группа → ключ (v1.7.6) ──────────────────

@router.get("/accounts/{account_id}/report/tree")
async def get_report_tree(
    account_id: int,
    period: str = Query("month"),
    date_from: Optional[str] = Query(None),
    date_to:   Optional[str] = Query(None),
    active_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """Иерархия кампания → группа → ключ с расходом и заявками на каждом уровне.

    Именно то, что просили для ИИ: вложенность, где заявки видны на всех
    уровнях, а не только там, где сматчился ключ.
    """
    curr_start, curr_end, _, _ = period_dates(period, date_from, date_to)

    kw_rows = (await db.execute(
        select(
            Campaign.id.label("cid"), Campaign.name.label("cname"),
            Campaign.strategy_type, Campaign.is_active,
            AdGroup.id.label("agid"), AdGroup.name.label("agname"),
            Keyword.id.label("kid"), Keyword.phrase.label("kphrase"),
            func.sum(KeywordStat.impressions).label("impr"),
            func.sum(KeywordStat.clicks).label("clicks"),
            func.sum(KeywordStat.spend).label("spend"),
        )
        .select_from(KeywordStat)
        .join(Keyword, Keyword.id == KeywordStat.keyword_id)
        .join(AdGroup, AdGroup.id == Keyword.ad_group_id)
        .join(Campaign, Campaign.id == AdGroup.campaign_id)
        .where(and_(
            KeywordStat.account_id == account_id,
            KeywordStat.date >= curr_start, KeywordStat.date <= curr_end,
            *( [Campaign.is_active == True] if active_only else [] ),  # noqa: E712
        ))
        .group_by(Campaign.id, Campaign.name, Campaign.strategy_type,
                  Campaign.is_active, AdGroup.id, AdGroup.name, Keyword.id, Keyword.phrase)
    )).all()

    camp_spend = await _campaign_spend(db, account_id, curr_start, curr_end)

    leads_camp = await _leads_by_dimension(db, account_id, "campaign", curr_start, curr_end)
    leads_ag = await _leads_by_dimension(db, account_id, "ad_group", curr_start, curr_end)
    leads_kw = await _leads_by_dimension(db, account_id, "keyword", curr_start, curr_end)

    tree: dict = {}
    for r in kw_rows:
        c = tree.setdefault(r.cid, {
            "campaign_id": r.cid, "campaign_name": r.cname,
            "strategy_type": r.strategy_type, "is_active": r.is_active,
            "impressions": 0, "clicks": 0, "spend": 0.0,
            "groups": {},
        })
        ag = c["groups"].setdefault(r.agid, {
            "ad_group_id": r.agid, "ad_group_name": r.agname,
            "impressions": 0, "clicks": 0, "spend": 0.0, "keywords": [],
        })
        impr = int(r.impr or 0); clk = int(r.clicks or 0); sp = float(r.spend or 0)
        kl = leads_kw.get(r.kid, {})
        ag["keywords"].append({
            "keyword_id": r.kid, "keyword_phrase": r.kphrase,
            "impressions": impr, "clicks": clk, "spend": round(sp, 2),
            "leads": kl.get("leads", 0), "sql": kl.get("sql", 0),
        })
        ag["impressions"] += impr; ag["clicks"] += clk; ag["spend"] += sp
        c["impressions"] += impr; c["clicks"] += clk; c["spend"] += sp

    known = set(tree)
    missing = [cid for cid in (set(camp_spend) | set(leads_camp)) if cid and cid not in known]
    if missing:
        extra = (await db.execute(
            select(Campaign.id, Campaign.name, Campaign.strategy_type, Campaign.is_active)
            .where(Campaign.id.in_(missing))
        )).all()
        for cid, name, strat, is_active in extra:
            if active_only and not is_active:
                continue
            cs = camp_spend.get(cid, {})
            tree[cid] = {
                "campaign_id": cid, "campaign_name": name,
                "strategy_type": strat, "is_active": is_active,
                "impressions": cs.get("impressions", 0),
                "clicks": cs.get("clicks", 0),
                "spend": cs.get("spend", 0.0),
                "groups": {}, "no_keywords": True,
            }

    out = []
    for cid, c in tree.items():
        cs = camp_spend.get(cid)
        if cs:
            c["impressions"], c["clicks"], c["spend"] = cs["impressions"], cs["clicks"], cs["spend"]
        cl = leads_camp.get(cid, {})
        c["leads"] = cl.get("leads", 0)
        c["sql"] = cl.get("sql", 0)
        c["spend"] = round(c["spend"], 2)
        groups_out = []
        for agid, ag in c["groups"].items():
            al = leads_ag.get(agid, {})
            ag["leads"] = al.get("leads", 0)
            ag["sql"] = al.get("sql", 0)
            ag["spend"] = round(ag["spend"], 2)
            ag["keywords"].sort(key=lambda k: k["spend"], reverse=True)
            groups_out.append(ag)
        groups_out.sort(key=lambda a: a["spend"], reverse=True)
        c["groups"] = groups_out
        out.append(c)
    out.sort(key=lambda c: c["spend"], reverse=True)

    return {
        "period_dates": {"from": curr_start.date().isoformat(),
                         "to": (curr_end - timedelta(days=1)).date().isoformat()},
        "campaigns": out,
        "campaign_spend_source": "campaign_stats" if camp_spend else "keyword_stats_fallback",
    }
