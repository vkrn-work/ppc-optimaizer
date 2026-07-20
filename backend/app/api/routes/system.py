"""Диагностика и health-check.

Выделено из монолитного routes.py без изменения логики.
"""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.models import (
    Account, Keyword, KeywordStat, MetrikaSnapshot,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ─── Diagnostics ─────────────────────────────

@router.get("/accounts/{account_id}/diagnostics")
async def get_diagnostics(account_id: int, db: AsyncSession = Depends(get_db)):
    acc_result = await db.execute(select(Account).where(Account.id == account_id))
    account = acc_result.scalar_one_or_none()
    kw_count   = await db.execute(select(func.count(Keyword.id)).where(Keyword.account_id == account_id))
    stat_count = await db.execute(select(func.count(KeywordStat.id)).where(KeywordStat.account_id == account_id))

    date_range = await db.execute(
        select(func.min(KeywordStat.date), func.max(KeywordStat.date))
        .where(KeywordStat.account_id == account_id)
    )
    dr = date_range.one()
    stats_date_from = dr[0].strftime("%d.%m.%Y") if dr[0] else None
    stats_date_to   = dr[1].strftime("%d.%m.%Y") if dr[1] else None
    stats_days = (dr[1] - dr[0]).days + 1 if dr[0] and dr[1] else 0

    from app.models.models import SearchQuery
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
        {"name": "Токен Директа", "ok": bool(account and account.oauth_token),
         "detail": "Настроен" if (account and account.oauth_token) else "Не настроен — перейдите в Кабинеты", "category": "config"},
        {"name": "Счётчик Метрики", "ok": bool(account and account.metrika_counter_id),
         "detail": f"Счётчик {account.metrika_counter_id}" if (account and account.metrika_counter_id) else "Не настроен", "category": "config"},
        {"name": "Последний сбор данных", "ok": bool(account and account.last_sync_at),
         "detail": account.last_sync_at.strftime("%d.%m.%Y %H:%M UTC") if (account and account.last_sync_at) else "Сбор ещё не запускался", "category": "sync"},
        {"name": "Ключевые слова в БД", "ok": (kw_count.scalar() or 0) > 0,
         "detail": f"{kw_count.scalar() or 0} ключей", "category": "data"},
        {"name": "Статистика в БД", "ok": (stat_count.scalar() or 0) > 0,
         "detail": f"{stat_count.scalar() or 0} записей" + (
             f" ({stats_date_from} — {stats_date_to}, {stats_days} дн.)" if stats_days > 0 else ""
         ), "category": "data"},
        {"name": "Поисковые фразы", "ok": sq_count > 0,
         "detail": f"{sq_count} запросов" if sq_count > 0 else "Появятся после следующего сбора", "category": "data"},
        {"name": "Данные Метрики", "ok": last_metrika is not None,
         "detail": f"Снапшот от {last_metrika.date.strftime('%d.%m.%Y')}" if last_metrika else "Нет данных — нужен сбор", "category": "data"},
    ]
    errors = [c for c in checks if not c["ok"]]
    return {
        "checks":       checks,
        "errors_count": len(errors),
        "last_sync_at": account.last_sync_at.isoformat() if (account and account.last_sync_at) else None,
        "stats_date_from": stats_date_from,
        "stats_date_to":   stats_date_to,
        "stats_days":      stats_days,
    }


# ─── Health ──────────────────────────────

@router.get("/health")
async def health():
    return {"status": "ok"}
