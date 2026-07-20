"""Аккаунты, синхронизация, запуск анализа, CRM-импорт, применение изменений.

Выделено из монолитного routes.py без изменения логики.
"""
from datetime import datetime
from typing import Optional, List
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.models import (
    Account, Campaign, AdGroup, Keyword, KeywordStat,
    AnalysisResult, KeywordMetrics, Suggestion, Hypothesis,
    Rule, Lead, SuggestionStatus,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ─── Accounts ──────────────────────────────

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
        Rule(account_id=account.id, name="Низкая позиция показа", condition_type="avg_position_gt",
             action_type="bid_increase", action_params={"pct": 30},
             priority="today", is_active=True),
        Rule(account_id=account.id, name="Падение трафика", condition_type="traffic_drop_gt",
             action_type="bid_increase", action_params={"pct": 25},
             priority="this_week", is_active=True),
        Rule(account_id=account.id, name="Нулевой CTR", condition_type="zero_ctr",
             action_type="check_ad", action_params={},
             priority="this_week", is_active=True),
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

@router.post("/accounts/{account_id}/run-analysis")
async def trigger_analysis(account_id: int, db: AsyncSession = Depends(get_db)):
    """Перезапуск анализа на существующих данных (без сбора из API)"""
    from app.core.tasks import run_analysis
    run_analysis.delay(account_id)
    return {"status": "started"}


@router.get("/llm-providers")
async def list_llm_providers():
    """Список LLM-провайдеров и их доступность (настроен ли ключ в .env) — для селектора на фронте."""
    from app.analyzers import llm_providers
    return [
        {
            "id": p,
            "label": {"claude": "Claude", "gemini": "Gemini", "groq": "Groq", "openrouter": "OpenRouter"}[p],
            "configured": llm_providers.provider_configured(p),
            "model": llm_providers.provider_model_name(p),
        }
        for p in llm_providers.PROVIDERS
    ]


@router.post("/accounts/{account_id}/run-llm-analysis")
async def trigger_llm_analysis(
    account_id: int,
    period_days: int = Query(28, description="За сколько дней брать данные"),
    provider: str = Query("claude", description="claude | gemini | groq | openrouter"),
    db: AsyncSession = Depends(get_db),
):
    """Запуск анализа через выбранного LLM-провайдера (объединяет статистику Директа и лиды CRM)."""
    from app.analyzers import llm_providers
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Account not found")
    if provider not in llm_providers.PROVIDERS:
        raise HTTPException(400, f"Неизвестный провайдер '{provider}'. Доступны: {llm_providers.PROVIDERS}")
    if not llm_providers.provider_configured(provider):
        raise HTTPException(400, f"API-ключ для '{provider}' не настроен на сервере (см. .env)")
    from app.core.tasks import run_llm_analysis
    run_llm_analysis.delay(account_id, period_days=period_days, provider=provider)
    return {"status": "started", "provider": provider, "message": f"ИИ-анализ запущен ({provider})"}


# ─── CRM-импорт ────────────────────────────

@router.post("/accounts/{account_id}/crm-import")
async def import_crm(
    account_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Загрузка выгрузки из CRM (CSV/XLSX) — записывает заявки в таблицу leads,
    связывая их с ключевыми словами по utm_term."""
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Account not found")

    if not file.filename or not file.filename.lower().endswith((".csv", ".xlsx", ".xlsm")):
        raise HTTPException(400, "Поддерживаются только файлы .csv, .xlsx, .xlsm")

    content = await file.read()
    from app.importers.crm_importer import import_crm_file, CRMImportError
    try:
        stats = await import_crm_file(db, account_id, file.filename, content)
    except CRMImportError as e:
        raise HTTPException(400, str(e))
    return {"status": "ok", **stats}


# ─── Применение изменения в Яндекс.Директ ──────────────

@router.post("/suggestions/{suggestion_id}/apply")
async def apply_suggestion_endpoint(suggestion_id: int, db: AsyncSession = Depends(get_db)):
    """Отправляет ОДОБРЕННОЕ (status=approved) предложение в кабинет Директа.
    Синхронный вызов — изменение обычно занимает <2с (один запрос к Direct API)."""
    res = await db.execute(select(Suggestion).where(Suggestion.id == suggestion_id))
    s = res.scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Suggestion not found")
    if s.status != SuggestionStatus.approved:
        raise HTTPException(400, f"Suggestion status is '{s.status.value}', expected 'approved'. Сначала одобрите его.")

    from app.core.tasks import _apply_suggestion_async
    result = await _apply_suggestion_async(suggestion_id)
    if result.get("status") == "error":
        raise HTTPException(400, result.get("detail", "apply failed"))
    return result
