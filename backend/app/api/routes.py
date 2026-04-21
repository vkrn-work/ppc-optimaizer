"""
API routes — PPC Optimizer
"""
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional, List
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, and_, desc, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.models import (
    Account, Campaign, AdGroup, Keyword, KeywordStat,
    AnalysisResult, KeywordMetrics, Suggestion, Hypothesis,
    Rule, Lead, SuggestionStatus,
)
from app.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def period_dates(period: str):
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
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


async def agg_kw_stats(db, account_id: int, date_from: datetime, date_to: datetime) -> dict:
    """Агрегировать keyword_stats за период → сводные KPI кабинета"""
    q = select(
        func.sum(KeywordStat.clicks).label("clicks"),
        func.sum(KeywordStat.impressions).label("impressions"),
        func.sum(KeywordStat.spend).label("spend"),
        func.avg(KeywordStat.avg_position).label("avg_position"),
        func.avg(KeywordStat.avg_click_position).label("avg_click_position"),
        func.avg(KeywordStat.traffic_volume).label("avg_traffic_volume"),
        # Новые поля v1.2
        func.avg(KeywordStat.bounce_rate).label("bounce_rate"),
        func.sum(KeywordStat.sessions).label("sessions"),
        func.avg(KeywordStat.weighted_ctr).label("weighted_ctr"),
    ).where(and_(
        KeywordStat.account_id == account_id,
        KeywordStat.date >= date_from,
        KeywordStat.date <= date_to,
    ))
    r = await db.execute(q)
    row = r.one()
    clicks      = int(row.clicks or 0)
    impressions = int(row.impressions or 0)
    spend       = float(row.spend or 0)
    # CPC = sum(spend)/sum(clicks) — правильный способ, не avg(avg_cpc)
    avg_cpc = round(spend / clicks, 2) if clicks > 0 else None
    # CTR = sum(clicks)/sum(impressions)*100
    ctr = round(clicks / impressions * 100, 2) if impressions > 0 else None
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


# ─── Accounts ─────────────────────────────────────────────────────────────────

class AccountCreate(BaseModel):
    name: str
    yandex_login: str
    oauth_token: str
    metrika_counter_id: Optional[str] = None
    target_cpl: Optional[float] = None
    target_cpql: Optional[float] = None


class AccountUpdate(BaseModel):
    oauth_token: Optional[str] = None
    target_cpl: Optional[float] = None
    target_cpql: Optional[float] = None
    metrika_counter_id: Optional[str] = None


class AccountResponse(BaseModel):
    id: int
    name: str
    yandex_login: str
    metrika_counter_id: Optional[str]
    target_cpl: Optional[float]
    target_cpql: Optional[float]
    is_active: bool
    last_sync_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("/accounts", response_model=List[AccountResponse])
async def list_accounts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).order_by(Account.created_at))
    return result.scalars().all()


@router.post("/accounts", response_model=AccountResponse)
async def create_account(data: AccountCreate, db: AsyncSession = Depends(get_db)):
    account = Account(**data.model_dump())
    db.add(account)
    await db.commit()
    await db.refresh(account)
    default_rules = [
        Rule(account_id=account.id, name="Низкая позиция показа", rule_type="bid_increase",
             condition={"field":"avg_position","op":"gt","value":3}, action={"type":"bid_increase","pct":30},
             priority=1, is_active=True),
        Rule(account_id=account.id, name="Падение трафика", rule_type="bid_increase",
             condition={"field":"traffic_drop_pct","op":"gt","value":30}, action={"type":"bid_increase","pct":25},
             priority=2, is_active=True),
        Rule(account_id=account.id, name="Нулевой CTR", rule_type="ad_check",
             condition={"field":"impressions","op":"gt","value":100,"and":{"field":"clicks","op":"eq","value":0}},
             action={"type":"check_ad"}, priority=3, is_active=True),
    ]
    for r in default_rules:
        db.add(r)
    await db.commit()
    return account


@router.patch("/accounts/{account_id}", response_model=AccountResponse)
async def update_account(account_id: int, data: AccountUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Account not found")
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(account, k, v)
    await db.commit()
    await db.refresh(account)
    return account


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import delete as sql_delete
    from app.models.models import (
        Campaign, AdGroup, Keyword, KeywordStat,
        Lead, AnalysisResult, KeywordMetrics, Suggestion, Hypothesis, Rule
    )
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Account not found")
    await db.execute(sql_delete(Hypothesis).where(
        Hypothesis.suggestion_id.in_(select(Suggestion.id).where(Suggestion.account_id == account_id))
    ))
    for model in [Suggestion, KeywordMetrics, AnalysisResult, Lead, Rule]:
        await db.execute(sql_delete(model).where(model.account_id == account_id))
    await db.execute(sql_delete(KeywordStat).where(KeywordStat.account_id == account_id))
    await db.execute(sql_delete(Keyword).where(Keyword.account_id == account_id))
    await db.execute(sql_delete(AdGroup).where(AdGroup.account_id == account_id))
    await db.execute(sql_delete(Campaign).where(Campaign.account_id == account_id))
    await db.delete(account)
    await db.commit()
    return {"status": "deleted", "id": account_id}


@router.post("/accounts/{account_id}/sync")
async def trigger_sync(
    account_id: int,
    days: int = Query(28, description="За сколько дней собирать статистику. 28 — стандарт, 90 — история"),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Account not found")
    from app.core.tasks import collect_account_data
    collect_account_data.delay(account_id, days=days)
    label = "ретроспективных данных" if days > 28 else "данных"
    return {"status": "started", "message": f"Сбор {label} запущен для кабинета '{account.name}' за {days} дней"}


# ─── Dashboard ────────────────────────────────────────────────────────────────

@router.get("/accounts/{account_id}/dashboard")
async def get_dashboard(
    account_id: int,
    period: str = Query("week", description="yesterday|3d|week|month"),
    db: AsyncSession = Depends(get_db),
):
    curr_start, curr_end, prev_start, prev_end = period_dates(period)

    acc_result = await db.execute(select(Account).where(Account.id == account_id))
    account = acc_result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Account not found")

    curr_kpi = await agg_kw_stats(db, account_id, curr_start, curr_end)
    prev_kpi = await agg_kw_stats(db, account_id, prev_start, prev_end)

    def mk_delta(key, invert=False):
        d = calc_delta(curr_kpi.get(key), prev_kpi.get(key))
        if d is None:
            return None
        return {"value": d, "is_good": (d < 0 if invert else d > 0)}

    kpi_with_deltas = {
        "clicks":             {"value": curr_kpi["clicks"], "delta": mk_delta("clicks"), "prev": prev_kpi["clicks"]},
        "impressions":        {"value": curr_kpi["impressions"], "delta": mk_delta("impressions"), "prev": prev_kpi["impressions"]},
        "spend":              {"value": curr_kpi["spend"], "delta": mk_delta("spend", invert=True), "prev": prev_kpi["spend"]},
        "ctr":                {"value": curr_kpi["ctr"], "delta": mk_delta("ctr"), "prev": prev_kpi["ctr"]},
        "avg_cpc":            {"value": curr_kpi["avg_cpc"], "delta": mk_delta("avg_cpc", invert=True), "prev": prev_kpi["avg_cpc"]},
        "avg_position":       {"value": curr_kpi["avg_position"], "delta": mk_delta("avg_position", invert=True), "prev": prev_kpi["avg_position"]},
        "avg_click_position": {"value": curr_kpi["avg_click_position"], "delta": mk_delta("avg_click_position", invert=True), "prev": prev_kpi["avg_click_position"]},
        "avg_traffic_volume": {"value": curr_kpi["avg_traffic_volume"], "delta": mk_delta("avg_traffic_volume"), "prev": prev_kpi["avg_traffic_volume"]},
        # Новые поля v1.2
        "bounce_rate":        {"value": curr_kpi["bounce_rate"], "delta": mk_delta("bounce_rate", invert=True), "prev": prev_kpi.get("bounce_rate")},
        "sessions":           {"value": curr_kpi["sessions"], "delta": mk_delta("sessions"), "prev": prev_kpi.get("sessions")},
        "weighted_ctr":       {"value": curr_kpi["weighted_ctr"], "delta": mk_delta("weighted_ctr"), "prev": prev_kpi.get("weighted_ctr")},
    }

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

    from app.models.models import MetrikaSnapshot
    metrika_result = await db.execute(
        select(MetrikaSnapshot)
        .where(MetrikaSnapshot.account_id == account_id)
        .order_by(desc(MetrikaSnapshot.date))
        .limit(1)
    )
    metrika = metrika_result.scalar_one_or_none()
    behavior = {}
    if metrika and metrika.data:
        all_by_day = metrika.data.get("by_day", [])
        curr_start_str = curr_start.date().isoformat()
        curr_end_str = curr_end.date().isoformat()
        period_by_day = [
            d for d in all_by_day
            if curr_start_str <= d.get("date", "") <= curr_end_str
        ]
        prev_by_day = [
            d for d in all_by_day
            if prev_start.date().isoformat() <= d.get("date", "") <= prev_end.date().isoformat()
        ]

        def avg_by_day(days, key):
            vals = [float(d[key]) for d in days if d.get(key) is not None]
            return round(sum(vals) / len(vals), 2) if vals else None

        def sum_by_day(days, key):
            return sum(float(d.get(key) or 0) for d in days)

        curr_visits = sum_by_day(period_by_day, "visits")
        curr_bounce = avg_by_day(period_by_day, "bounceRate")
        curr_duration = avg_by_day(period_by_day, "avgVisitDurationSeconds")
        curr_depth = avg_by_day(period_by_day, "pageDepth")

        prev_visits = sum_by_day(prev_by_day, "visits") if prev_by_day else None
        prev_bounce = avg_by_day(prev_by_day, "bounceRate") if prev_by_day else None
        prev_duration = avg_by_day(prev_by_day, "avgVisitDurationSeconds") if prev_by_day else None

        if not curr_visits and not period_by_day:
            s = metrika.data.get("summary", {})
            curr_visits = s.get("visits")
            curr_bounce = s.get("bounceRate")
            curr_duration = s.get("avgVisitDurationSeconds")
            curr_depth = s.get("pageDepth")

        bounce = curr_bounce
        duration = curr_duration
        depth = curr_depth
        quality = None
        if bounce is not None:
            b = (1 - (bounce or 0) / 100) * 0.4
            t = min((duration or 0) / 180, 1) * 0.3
            d = min((depth or 0) / 3, 1) * 0.2
            quality = round((b + t + d) * 100 / 0.9)

        def mk_m_delta(curr_v, prev_v, invert=False):
            if not prev_v or prev_v == 0 or curr_v is None:
                return None
            d = (curr_v - prev_v) / abs(prev_v) * 100
            return {"value": round(d, 1), "is_good": (d < 0 if invert else d > 0)}

        behavior = {
            "has_metrika": True,
            "visits": curr_visits,
            "visits_delta": mk_m_delta(curr_visits, prev_visits),
            "bounce_rate": bounce,
            "bounce_delta": mk_m_delta(bounce, prev_bounce, invert=True),
            "page_depth": depth,
            "avg_duration": duration,
            "duration_delta": mk_m_delta(duration, prev_duration),
            "quality_score": quality,
            "by_day": period_by_day,
            "devices": metrika.data.get("devices", []),
            "regions": metrika.data.get("regions", [])[:10],
            "by_weekday": metrika.data.get("by_weekday", []),
            "by_hour": metrika.data.get("by_hour", []),
            "landings": metrika.data.get("landings", [])[:10],
            "browsers": metrika.data.get("browsers", [])[:10],
        }

    daily_q = await db.execute(
        select(
            KeywordStat.date,
            func.sum(KeywordStat.clicks).label("clicks"),
            func.sum(KeywordStat.impressions).label("impressions"),
            func.sum(KeywordStat.spend).label("spend"),
            func.avg(KeywordStat.avg_position).label("avg_position"),
        )
        .where(
            KeywordStat.account_id == account_id,
            KeywordStat.date >= curr_start,
            KeywordStat.date <= curr_end,
        )
        .group_by(KeywordStat.date)
        .order_by(KeywordStat.date)
    )
    daily_stats = []
    for r in daily_q:
        cl = int(r.clicks or 0)
        im = int(r.impressions or 0)
        daily_stats.append({
            "date": r.date.strftime("%Y-%m-%d"),
            "clicks": cl,
            "impressions": im,
            "spend": round(float(r.spend or 0), 2),
            "avg_position": round(float(r.avg_position), 2) if r.avg_position else None,
            "ctr": round(cl / im * 100, 2) if im > 0 else None,
        })

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

    return {
        "account_id": account_id,
        "period": period,
        "period_dates": {
            "curr_start": curr_start.date().isoformat(),
            "curr_end": curr_end.date().isoformat(),
            "prev_start": prev_start.date().isoformat(),
            "prev_end": prev_end.date().isoformat(),
        },
        "ad_kpi": kpi_with_deltas,
        "active_campaigns": active_campaigns,
        "behavior": behavior,
        "problems": analysis.problems if analysis else [],
        "opportunities": analysis.opportunities if analysis else [],
        "analysis_at": analysis.created_at.isoformat() if analysis else None,
        # Сводка анализа со статистикой сигналов
        "analysis_summary": analysis.summary if analysis else {},
        "suggestions_pending": today_suggestions.scalar() or 0,
        "top_campaigns": top_campaigns,
        "daily_stats": daily_stats,
        "total_campaigns": total_campaigns,
        "period_label": {
            "yesterday": "Вчера vs среднее за 14 дней",
            "3d": "3 дня vs предыдущие 3 дня",
            "week": "7 дней vs предыдущие 7 дней",
            "month": "30 дней vs предыдущие 30 дней",
        }.get(period, period),
    }


# ─── Campaigns ────────────────────────────────────────────────────────────────

@router.get("/accounts/{account_id}/campaigns")
async def get_campaigns(
    account_id: int,
    period: str = Query("week"),
    active_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """
    Список кампаний со статистикой, дельтами и агрегированными сигналами.

    Метрики per кампания:
      clicks, impressions, spend, ctr, avg_cpc
      avg_position, avg_click_position, traffic_volume
      bounce_rate (из Метрики по кампании)
      click_delta, position_delta, spend_delta  — дельты к предыдущему периоду
      signals_count — количество активных сигналов
      has_epk_collapse — флаг ЕПК-обвала
    """
    curr_start, curr_end, prev_start, prev_end = period_dates(period)

    camp_q = select(Campaign).where(Campaign.account_id == account_id)
    if active_only:
        camp_q = camp_q.where(Campaign.is_active == True)
    campaigns_result = await db.execute(camp_q.order_by(Campaign.name))
    campaigns = campaigns_result.scalars().all()

    # Текущий период
    def make_stats_q(date_from, date_to):
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
                KeywordStat.date >= date_from,
                KeywordStat.date <= date_to,
            )
            .group_by(Campaign.id)
        )

    curr_q   = await db.execute(make_stats_q(curr_start, curr_end))
    curr_map = {r.id: r for r in curr_q}

    prev_q   = await db.execute(make_stats_q(prev_start, prev_end))
    prev_map = {r.id: r for r in prev_q}

    # Сигналы из последнего анализа — группируем по campaign_id через keyword
    analysis_result = await db.execute(
        select(AnalysisResult)
        .where(AnalysisResult.account_id == account_id)
        .order_by(desc(AnalysisResult.created_at))
        .limit(1)
    )
    analysis = analysis_result.scalar_one_or_none()

    # Маппинг keyword_id → campaign_id
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

    # Сигналы по кампании
    signals_by_camp: dict[int, list] = {}
    if analysis and analysis.problems:
        for p in analysis.problems:
            kw_id = p.get("keyword_id")
            # Для сигналов уровня кампании (epk_bid_collapse) entity_id = camp_id
            camp_id_direct = p.get("entity_id") if p.get("entity_type") == "campaign" else None
            camp_id = camp_id_direct or camp_by_kw.get(kw_id)
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
        cpos= round(float(s.avg_click_position), 2) if s and s.avg_click_position else None
        traf= round(float(s.traffic_volume))         if s and s.traffic_volume else None
        br  = round(float(s.bounce_rate), 1)         if s and s.bounce_rate else None
        sess= int(s.sessions or 0)    if s else None

        p_cl  = int(ps.clicks or 0)   if ps else 0
        p_sp  = float(ps.spend or 0)  if ps else 0
        p_pos = float(ps.avg_position) if ps and ps.avg_position else None

        cpc = round(sp / cl, 2) if cl > 0 else None
        ctr = round(cl / im * 100, 2) if im > 0 else None

        camp_signals = signals_by_camp.get(c.id, [])
        has_epk = any(p.get("type") == "epk_bid_collapse" for p in camp_signals)

        result.append({
            "id":                c.id,
            "direct_id":         c.direct_id,
            "name":              c.name,
            "campaign_type":     c.campaign_type,
            "strategy_type":     c.strategy_type,
            "status":            c.status,
            "is_active":         c.is_active,
            # ── Текущие метрики ────────────────────────────────────────
            "spend":             round(sp, 2),
            "clicks":            cl,
            "impressions":       im,
            "avg_cpc":           cpc,
            "ctr":               ctr,
            "avg_position":      pos,
            "avg_click_position": cpos,
            "traffic_volume":    traf,
            "bounce_rate":       br,
            "sessions":          sess,
            # ── Дельты ─────────────────────────────────────────────────
            "click_delta":       calc_delta(cl, p_cl),
            "spend_delta":       calc_delta(sp, p_sp),
            "position_delta":    calc_delta(p_pos, pos, invert=True),
            # ── Сигналы ────────────────────────────────────────────────
            "signals_count":     len(camp_signals),
            "signals_critical":  sum(1 for s in camp_signals if s.get("severity") == "critical"),
            "signals_warning":   sum(1 for s in camp_signals if s.get("severity") == "warning"),
            "has_epk_collapse":  has_epk,
            "top_signal":        camp_signals[0] if camp_signals else None,
        })

    # Сортировка: сначала с критичными сигналами, потом по расходу
    result.sort(key=lambda x: (
        0 if x["signals_critical"] > 0 else (1 if x["signals_count"] > 0 else 2),
        -x["spend"]
    ))
    return result

# ─── Ad Groups ────────────────────────────────────────────────────────────────

@router.get("/accounts/{account_id}/ad-groups")
async def get_ad_groups(
    account_id: int,
    campaign_id: Optional[int] = None,
    period: str = Query("week"),
    db: AsyncSession = Depends(get_db),
):
    """Группы объявлений с базовой статистикой"""
    curr_start, curr_end, _, _ = period_dates(period)

    q = select(AdGroup).where(AdGroup.account_id == account_id)
    if campaign_id:
        q = q.where(AdGroup.campaign_id == campaign_id)
    result = await db.execute(q.order_by(AdGroup.name))
    groups = result.scalars().all()
    group_ids = [g.id for g in groups]

    if not group_ids:
        return []

    # Статистика по группам через ключи
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

    # Количество ключей на группу
    kw_count_q = await db.execute(
        select(Keyword.ad_group_id, func.count(Keyword.id).label("cnt"))
        .where(Keyword.ad_group_id.in_(group_ids))
        .group_by(Keyword.ad_group_id)
    )
    kw_map = {r.ad_group_id: r.cnt for r in kw_count_q}

    return [{
        "id": g.id,
        "name": g.name,
        "campaign_id": g.campaign_id,
        "status": g.status,
        "keywords_count": kw_map.get(g.id, 0),
        "spend": round(float(stats_map[g.id].spend or 0), 2) if g.id in stats_map else 0,
        "clicks": int(stats_map[g.id].clicks or 0) if g.id in stats_map else 0,
        "impressions": int(stats_map[g.id].impressions or 0) if g.id in stats_map else 0,
    } for g in groups]


# ─── Keywords ─────────────────────────────────────────────────────────────────

@router.get("/accounts/{account_id}/keywords")
async def get_keywords(
    account_id: int,
    period: str = Query("week"),
    campaign_id: Optional[int] = None,
    ad_group_id: Optional[int] = None,
    search: Optional[str] = None,
    active_only: bool = Query(False),
    limit: int = 500,
    db: AsyncSession = Depends(get_db),
):
    """
    Список ключевых слов со статистикой, дельтами и сигналами.

    Возвращаемые метрики:
      Базовые:       clicks, impressions, spend, ctr, avg_cpc
      Ставка:        current_bid, avg_bid (AvgEffectiveBid)
      Позиции:       avg_position, avg_click_position
      Объём рынка:   traffic_volume (AvgTrafficVolume 0–150)
      Поведение:     bounce_rate, sessions, weighted_ctr
      Расчётные:     click_position_gap, traffic_quality_score
      Сравнение:     click_delta, bid_delta, position_delta
      Рекомендации:  recommended_bid, signal (из последнего анализа)
    """
    curr_start, curr_end, prev_start, prev_end = period_dates(period)

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

    # Текущий период
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

    # Предыдущий период (для дельт)
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

    # Сигналы из последнего анализа
    analysis_result = await db.execute(
        select(AnalysisResult)
        .where(AnalysisResult.account_id == account_id)
        .order_by(desc(AnalysisResult.created_at))
        .limit(1)
    )
    analysis    = analysis_result.scalar_one_or_none()
    signal_map  = {}
    if analysis and analysis.problems:
        for p in analysis.problems:
            kid = p.get("keyword_id")
            if kid:
                signal_map[kid] = p

    result = []
    for kw in keywords:
        cs = curr_map.get(kw.id)
        ps = prev_map.get(kw.id)

        clicks        = int(cs.clicks or 0) if cs else 0
        prev_clicks   = int(ps.clicks or 0) if ps else 0
        impressions   = int(cs.impressions or 0) if cs else 0
        spend         = float(cs.spend or 0) if cs else 0
        avg_pos       = round(float(cs.avg_position), 2) if cs and cs.avg_position else None
        avg_cpos      = round(float(cs.avg_click_position), 2) if cs and cs.avg_click_position else None
        traf          = round(float(cs.traffic_volume)) if cs and cs.traffic_volume else None
        avg_bid       = round(float(cs.avg_bid), 2) if cs and cs.avg_bid else None
        w_ctr         = round(float(cs.weighted_ctr), 2) if cs and cs.weighted_ctr else None
        w_impr        = int(cs.weighted_impressions or 0) if cs else None
        bounce_rate   = round(float(cs.bounce_rate), 1) if cs and cs.bounce_rate else None
        sessions      = int(cs.sessions or 0) if cs else None

        # Дельты
        click_delta   = calc_delta(clicks, prev_clicks)
        pos_delta     = calc_delta(
            float(ps.avg_position) if ps and ps.avg_position else None,
            avg_pos,
            invert=True  # позиция: меньше = лучше
        ) if ps else None
        bid_delta     = calc_delta(avg_bid, float(ps.avg_bid) if ps and ps.avg_bid else None)

        # CPC и CTR через суммы (правильный способ)
        avg_cpc       = round(spend / clicks, 2) if clicks > 0 else None
        ctr           = round(clicks / impressions * 100, 2) if impressions > 0 else None

        # Позиционный разрыв
        pos_gap = round(avg_cpos - avg_pos, 2) if avg_pos and avg_cpos else None

        # Рекомендованная ставка (из анализа или по формуле позиции)
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

        # Скоринг качества трафика 0–100 для этого ключа
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
            # ── Ставки ────────────────────────────────────────────────
            "current_bid":      float(kw.current_bid) if kw.current_bid else None,
            "avg_bid":          avg_bid,
            "recommended_bid":  recommended_bid,
            "bid_delta":        bid_delta,
            # ── Трафик ────────────────────────────────────────────────
            "clicks":           clicks,
            "impressions":      impressions,
            "spend":            round(spend, 2),
            "ctr":              ctr,
            "avg_cpc":          avg_cpc,
            # ── Позиции ───────────────────────────────────────────────
            "avg_position":     avg_pos,
            "avg_click_position": avg_cpos,
            "click_position_gap": pos_gap,
            # ── Объём рынка ───────────────────────────────────────────
            "traffic_volume":   traf,
            "weighted_ctr":     w_ctr,
            "weighted_impressions": w_impr,
            # ── Поведение ─────────────────────────────────────────────
            "bounce_rate":      bounce_rate,
            "sessions":         sessions,
            # ── Дельты к предыдущему периоду ──────────────────────────
            "click_delta":      click_delta,
            "position_delta":   pos_delta,
            # ── Качество и сигналы ────────────────────────────────────
            "traffic_quality":  traffic_quality,
            "signal":           sig,
        })

    result.sort(key=lambda x: (0 if x["signal"] else 1, -(x["spend"] or 0)))
    return result

# ─── Analysis ─────────────────────────────────────────────────────────────────

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
        "period_end": a.period_end.isoformat() if a.period_end else None,
        "summary": a.summary,
        "problems": a.problems or [],
        "opportunities": a.opportunities or [],
    } for a in analyses]


# ─── Suggestions ──────────────────────────────────────────────────────────────

@router.get("/accounts/{account_id}/suggestions")
async def get_suggestions(
    account_id: int,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    analysis_result = await db.execute(
        select(AnalysisResult)
        .where(AnalysisResult.account_id == account_id)
        .order_by(desc(AnalysisResult.created_at))
        .limit(1)
    )
    analysis = analysis_result.scalar_one_or_none()
    if not analysis:
        return []

    items = [
        *[{**p, "_cat": "problem", "id": f"p_{i}"} for i, p in enumerate(analysis.problems or [])],
        *[{**o, "_cat": "opportunity", "id": f"o_{i}", "severity": "success"} for i, o in enumerate(analysis.opportunities or [])],
    ]
    return items


@router.post("/suggestions/{suggestion_id}/action")
async def action_suggestion(suggestion_id: str, data: dict, db: AsyncSession = Depends(get_db)):
    action = data.get("action", "accept")
    account_id = data.get("account_id")
    suggestion_data = data.get("suggestion", {})

    if action == "accept" and account_id:
        hypothesis = Hypothesis(
            account_id=account_id,
            suggestion_id=None,
            object_type=suggestion_data.get("type", "keyword"),
            object_id=suggestion_data.get("keyword_id"),
            description=suggestion_data.get("description", ""),
            change_description=suggestion_data.get("action", ""),
            forecast=suggestion_data.get("description", ""),
            source="algorithm",
            verdict="pending",
            applied_at=datetime.utcnow(),
            track_until=datetime.utcnow() + timedelta(days=7),
        )
        db.add(hypothesis)
        await db.commit()
        await db.refresh(hypothesis)
        return {"status": "created", "hypothesis_id": hypothesis.id}
    return {"status": "rejected"}


# ─── Hypotheses ───────────────────────────────────────────────────────────────

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
        v = h.verdict.value if h.verdict else "pending"
        if v == "pending": return "planned"
        if v == "positive": return "success"
        if v == "negative": return "failed"
        if v == "neutral": return "neutral"
        return v

    return [{
        "id": h.id,
        "object_type": h.object_type,
        "phrase": h.description.split(":")[0] if h.description and ":" in h.description else h.description,
        "description": h.description,
        "change_description": h.change_description,
        "forecast": h.forecast,
        "source": h.source,
        "status": h_status(h),
        "verdict": h.verdict.value if h.verdict else None,
        "created_at": h.applied_at.isoformat() if h.applied_at else None,
        "check_after": h.track_until.isoformat() if h.track_until else None,
        "metrics_before": h.metrics_before,
        "metrics_after": h.metrics_after,
        "report": h.report,
        "delta_percent": float(h.delta_percent) if h.delta_percent else None,
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
        verdict="pending",
        applied_at=datetime.utcnow(),
        track_until=datetime.utcnow() + timedelta(days=7),
    )
    db.add(hypothesis)
    await db.commit()
    await db.refresh(hypothesis)
    return {"id": hypothesis.id, "status": "created"}


# ─── Rules ────────────────────────────────────────────────────────────────────

@router.get("/accounts/{account_id}/rules")
async def get_rules(account_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Rule).where(Rule.account_id == account_id).order_by(Rule.priority)
    )
    rules = result.scalars().all()
    return [{
        "id": r.id,
        "name": r.name,
        "rule_type": r.rule_type,
        "condition": r.condition,
        "action": r.action,
        "priority": r.priority,
        "is_active": r.is_active,
    } for r in rules]


# ─── Metrika snapshot ─────────────────────────────────────────────────────────

@router.get("/accounts/{account_id}/metrika-snapshot")
async def get_metrika_snapshot(account_id: int, db: AsyncSession = Depends(get_db)):
    from app.models.models import MetrikaSnapshot
    result = await db.execute(
        select(MetrikaSnapshot)
        .where(MetrikaSnapshot.account_id == account_id)
        .order_by(desc(MetrikaSnapshot.date))
        .limit(1)
    )
    snapshot = result.scalar_one_or_none()
    if not snapshot:
        raise HTTPException(404, "No Metrika data yet")
    return {"date": snapshot.date.isoformat(), "data": snapshot.data}


# ─── Search queries ───────────────────────────────────────────────────────────

@router.get("/accounts/{account_id}/search-queries")
async def get_search_queries(
    account_id: int,
    suggest: str = "",
    campaign_id: Optional[int] = None,
    search: Optional[str] = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
):
    from app.models.models import SearchQuery
    from sqlalchemy import or_

    q = select(SearchQuery).where(SearchQuery.account_id == account_id)

    if search:
        q = q.where(SearchQuery.query.ilike(f"%{search}%"))

    NEGATIVE_SIGNALS = ['стандарт', 'что такое', 'скачать', 'характеристики',
                        'описание', 'гост', 'нормативы', 'документ', 'pdf']
    COMMERCIAL_SIGNALS = ['купить', 'цена', 'поставка', 'заказ', 'прайс',
                          'производитель', 'поставщик', 'мм', 'дюйм',
                          'лист', 'труба', 'прокат', 'сталь', 'полоса', 'лента']

    if suggest == "negatives":
        neg_filters = [SearchQuery.query.ilike(f"%{s}%") for s in NEGATIVE_SIGNALS]
        q = q.where(
            SearchQuery.clicks >= 2,
            or_(*neg_filters) if neg_filters else True,
        ).order_by(SearchQuery.spend.desc())
    elif suggest == "new_keywords":
        q = q.where(
            SearchQuery.clicks >= 2,
            SearchQuery.match_type != 'EXACT',
        ).order_by(SearchQuery.clicks.desc())
    else:
        q = q.order_by(SearchQuery.clicks.desc())

    q = q.limit(limit)
    result = await db.execute(q)
    rows = result.scalars().all()

    def score_query(query_text):
        qt = (query_text or "").lower()
        score = 50
        for sig in COMMERCIAL_SIGNALS:
            if sig in qt: score += 10
        for sig in NEGATIVE_SIGNALS:
            if sig in qt: score -= 20
        import re
        if re.search(r'\d+[xх×]\d+|\d+мм|\d+"\s|\d/\d', qt): score += 20
        return max(0, min(100, score))

    return [{
        "id": r.id,
        "query": r.query,
        "keyword_phrase": r.keyword_phrase,
        "match_type": r.match_type,
        "clicks": r.clicks,
        "impressions": r.impressions,
        "spend": float(r.spend) if r.spend else 0,
        "ctr": round(float(r.ctr), 2) if r.ctr else None,
        "avg_position": round(float(r.avg_position), 2) if r.avg_position else None,
        "avg_click_position": round(float(r.avg_click_position), 2) if r.avg_click_position else None,
        "commercial_score": score_query(r.query),
        "date": r.date.isoformat() if r.date else None,
    } for r in rows]


# ─── Diagnostics ──────────────────────────────────────────────────────────────

@router.get("/accounts/{account_id}/diagnostics")
async def get_diagnostics(account_id: int, db: AsyncSession = Depends(get_db)):
    acc_result = await db.execute(select(Account).where(Account.id == account_id))
    account = acc_result.scalar_one_or_none()

    kw_count = await db.execute(select(func.count(Keyword.id)).where(Keyword.account_id == account_id))
    stat_count = await db.execute(select(func.count(KeywordStat.id)).where(KeywordStat.account_id == account_id))

    from app.models.models import MetrikaSnapshot, SearchQuery
    ms_result = await db.execute(
        select(MetrikaSnapshot).where(MetrikaSnapshot.account_id == account_id)
        .order_by(desc(MetrikaSnapshot.date)).limit(1)
    )
    last_metrika = ms_result.scalar_one_or_none()

    sq_count_r = await db.execute(
        select(func.count(SearchQuery.id)).where(SearchQuery.account_id == account_id)
    )
    sq_count = sq_count_r.scalar() or 0

    checks = [
        {
            "name": "Токен Директа",
            "ok": bool(account and account.oauth_token),
            "detail": "Настроен" if (account and account.oauth_token) else "Не настроен — перейдите в Кабинеты",
            "category": "config",
        },
        {
            "name": "Счётчик Метрики",
            "ok": bool(account and account.metrika_counter_id),
            "detail": f"Счётчик {account.metrika_counter_id}" if (account and account.metrika_counter_id) else "Не настроен",
            "category": "config",
        },
        {
            "name": "Последний сбор данных",
            "ok": bool(account and account.last_sync_at),
            "detail": account.last_sync_at.strftime("%d.%m.%Y %H:%M UTC") if (account and account.last_sync_at) else "Сбор ещё не запускался",
            "category": "sync",
        },
        {
            "name": "Ключевые слова в БД",
            "ok": (kw_count.scalar() or 0) > 0,
            "detail": f"{kw_count.scalar() or 0} ключей",
            "category": "data",
        },
        {
            "name": "Статистика в БД",
            "ok": (stat_count.scalar() or 0) > 0,
            "detail": f"{stat_count.scalar() or 0} записей",
            "category": "data",
        },
        {
            "name": "Поисковые фразы",
            "ok": sq_count > 0,
            "detail": f"{sq_count} запросов" if sq_count > 0 else "Появятся после следующего сбора",
            "category": "data",
        },
        {
            "name": "Данные Метрики",
            "ok": last_metrika is not None,
            "detail": f"Снапшот от {last_metrika.date.strftime('%d.%m.%Y')}" if last_metrika else "Нет данных — нужен сбор",
            "category": "data",
        },
    ]

    errors = [c for c in checks if not c["ok"]]
    return {
        "checks": checks,
        "errors_count": len(errors),
        "last_sync_at": account.last_sync_at.isoformat() if (account and account.last_sync_at) else None,
    }


# ─── Health ───────────────────────────────────────────────────────────────────
