from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.ENVIRONMENT == "development",
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    from app.models.models import Base
    async with engine.begin() as conn:
        # Создать новые таблицы
        await conn.run_sync(Base.metadata.create_all)
        # Применить миграции вручную
        await _run_migrations(conn)


async def _run_migrations(conn):
    """Добавить недостающие колонки в существующие таблицы"""
    migrations = [
        # campaigns
        "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS strategy_type VARCHAR(50)",
        # keyword_stats
        "ALTER TABLE keyword_stats ADD COLUMN IF NOT EXISTS avg_click_position NUMERIC(5,2)",
        "ALTER TABLE keyword_stats ADD COLUMN IF NOT EXISTS ctr NUMERIC(8,4)",
        "ALTER TABLE keyword_stats ADD COLUMN IF NOT EXISTS ad_id VARCHAR(100)",
        # search_queries — создаётся через create_all, но индексы могут не создаться
        "CREATE INDEX IF NOT EXISTS ix_sq_account_date ON search_queries (account_id, date)",
        "CREATE INDEX IF NOT EXISTS ix_sq_query ON search_queries (account_id, query)",
    ]
    for sql in migrations:
        try:
            await conn.execute(text(sql))
            logger.info(f"Migration OK: {sql[:60]}")
        except Exception as e:
            logger.warning(f"Migration skip: {sql[:60]} — {e}")
