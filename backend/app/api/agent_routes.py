"""
Страница «Задачи ИИ» — свободная текстовая команда пользователя
(например, «добавь в рекламу трубу стальную электросварную 89х3.5») превращается
в конкретный план: список новых ключевых фраз + минус-слова + целевая группа
объявлений. Сделан отдельным файлом по тому же принципу, что и debug_routes.py —
чтобы не трогать огромный routes.py ради нового небольшого набора эндпоинтов.

ВАЖНО: этот роутер НИЧЕГО не пишет в Яндекс Директ напрямую. Он только создаёт
pending Suggestion(s) — запись в кабинет происходит через уже существующий
approve/apply-пайплайн (POST /suggestions/{id}/action, затем POST
/suggestions/{id}/apply), тот же самый, которым работает страница «Предложения».
Это осознанное решение по безопасности: у бота нет права сразу менять живой
кабинет с реальным бюджетом без подтверждения человека.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.models import Account, AnalysisResult

router = APIRouter()
logger = logging.getLogger(__name__)


class AgentCommandRequest(BaseModel):
    command: str
    provider: str = "claude"


@router.post("/accounts/{account_id}/agent-command")
async def run_agent_command(account_id: int, body: AgentCommandRequest, db: AsyncSession = Depends(get_db)):
    """Принимает свободную команду, генерирует план через выбранный LLM-провайдер
    и создаёт pending-предложения (add_keywords [+ add_negatives]) для одобрения
    на странице «ИИ-анализ» → «Предложения». Ничего не применяет сразу."""
    from app.analyzers import llm_providers
    from app.analyzers.agent_command import CommandAgent, CommandAgentError

    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Account not found")

    command = (body.command or "").strip()
    if not command:
        raise HTTPException(400, "Пустая команда")
    if body.provider not in llm_providers.PROVIDERS:
        raise HTTPException(400, f"Неизвестный провайдер '{body.provider}'. Доступны: {llm_providers.PROVIDERS}")
    if not llm_providers.provider_configured(body.provider):
        raise HTTPException(400, f"API-ключ для '{body.provider}' не настроен на сервере (см. .env)")

    agent = CommandAgent(db, account_id)
    try:
        outcome = await agent.run_command(command, body.provider)
    except CommandAgentError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("agent_command failed")
        raise HTTPException(500, f"Неожиданная ошибка: {e}")
    return outcome


@router.get("/accounts/{account_id}/agent-commands")
async def get_agent_commands(account_id: int, limit: int = 20, db: AsyncSession = Depends(get_db)):
    """История команд, отданных ИИ-агенту на этой странице — для показа лога
    последних задач и их результата (создано/не нашёл группу/ошибка)."""
    result = await db.execute(
        select(AnalysisResult)
        .where(AnalysisResult.account_id == account_id)
        .order_by(desc(AnalysisResult.created_at))
        .limit(200)
    )
    rows = [a for a in result.scalars().all() if (a.summary or {}).get("source") == "agent_command"][:limit]
    return [{
        "id": a.id,
        "created_at": a.created_at.isoformat(),
        "command": (a.summary or {}).get("command"),
        "provider": (a.summary or {}).get("provider"),
        "status": (a.summary or {}).get("status"),
        "target_ad_group": (a.summary or {}).get("target_ad_group_name"),
        "suggested_ad_group_name": (a.summary or {}).get("suggested_ad_group_name"),
        "keywords": (a.summary or {}).get("keywords", []),
        "negative_keywords": (a.summary or {}).get("negative_keywords", []),
        "rationale": (a.summary or {}).get("rationale"),
        "suggestion_ids": (a.summary or {}).get("suggestion_ids", []),
        "error": (a.summary or {}).get("error"),
    } for a in rows]
