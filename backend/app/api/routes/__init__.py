"""Агрегатор API-роутов.

Ранее весь код лежал в одном файле routes.py (1796 строк).
Импорт `from app.api.routes import router` продолжает работать как раньше.
"""
from fastapi import APIRouter

from app.api.routes import accounts, dashboard, campaigns, keywords, suggestions, hypotheses, search_queries, reports, system

router = APIRouter()

router.include_router(accounts.router)
router.include_router(dashboard.router)
router.include_router(campaigns.router)
router.include_router(keywords.router)
router.include_router(suggestions.router)
router.include_router(hypotheses.router)
router.include_router(search_queries.router)
router.include_router(reports.router)
router.include_router(system.router)

__all__ = ["router"]
