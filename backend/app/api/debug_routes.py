"""
Временный диагностический роутер — только чтение из information_schema,
ничего не меняет. Сделан отдельным файлом, чтобы не трогать огромный routes.py
ради одного временного эндпоинта. Можно удалить после того как схема leads
полностью синхронизируется с моделью.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db

router = APIRouter()


@router.get("/_debug/table-schema/{table_name}")
async def debug_table_schema(table_name: str, db: AsyncSession = Depends(get_db)):
    """Реальные колонки таблицы из information_schema — чтобы не гадать по логам,
    чего не хватает в БД. Только SELECT, ничего не меняет."""
    result = await db.execute(
        text(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_name = :t "
            "ORDER BY ordinal_position"
        ),
        {"t": table_name},
    )
    rows = [dict(r._mapping) for r in result.all()]
    return {"table": table_name, "columns": rows}
