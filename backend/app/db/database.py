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
        # metrika_snapshots создаётся через create_all
        "CREATE INDEX IF NOT EXISTS ix_ms_account_date ON metrika_snapshots (account_id, date)",
        # Фильтровать только активные кампании в представлении
        "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS state VARCHAR(50)",
        # CHANGED: v1.2 колонки — раньше отсутствовали в миграциях, из-за
        # чего на уже существующей БД сбор статистики падал на INSERT этих полей.
        "ALTER TABLE keyword_stats ADD COLUMN IF NOT EXISTS weighted_impressions INTEGER",
        "ALTER TABLE keyword_stats ADD COLUMN IF NOT EXISTS weighted_ctr NUMERIC(8,4)",
        "ALTER TABLE keyword_stats ADD COLUMN IF NOT EXISTS bounce_rate NUMERIC(6,2)",
        "ALTER TABLE keyword_stats ADD COLUMN IF NOT EXISTS sessions INTEGER",
        "ALTER TABLE keyword_stats ADD COLUMN IF NOT EXISTS avg_bid NUMERIC(10,2)",
        "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS epk_collapse_detected BOOLEAN DEFAULT FALSE",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS analysis_config JSONB DEFAULT '{}'::jsonb",
        # enum hypothesisverdict: добавить значение neutral, если его нет
        "ALTER TYPE hypothesisverdict ADD VALUE IF NOT EXISTS 'neutral'",
        # v1.4.0: воронка lead/MQL/SQL на leads (см. app/importers/crm_importer.py)
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS raw_status VARCHAR(255)",
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS is_mql BOOLEAN DEFAULT FALSE",
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS is_sql BOOLEAN DEFAULT FALSE",
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS source_raw TEXT",
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS matched_by VARCHAR(50)",
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS matched_ad_id VARCHAR(100)",
        # v1.4.1 HOTFIX: utm_medium давно объявлена в модели Lead, но никогда не
        # была в миграциях — таблица leads создавалась create_all() до того, как
        # это поле попало в models.py. Первый же реальный импорт CRM падал с
        # UndefinedColumnError, т.к. ORM включает все объявленные поля в INSERT.
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS utm_medium VARCHAR(255)",
        # v1.4.2 HOTFIX — то же самое для всех остальных полей Lead, которые обнаружились
        # тоже отсутствующими в базе (первый реальный импорт упал на revenue сразу
        # после фикса utm_medium — таблица очевидно никогда полностью не синхронизировалась с моделью).
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS external_id VARCHAR(255)",
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS keyword_id INTEGER",
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS client_id VARCHAR(255)",
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS utm_source VARCHAR(255)",
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS utm_campaign VARCHAR(255)",
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS utm_term VARCHAR(500)",
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS revenue NUMERIC(14,2)",
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS created_at TIMESTAMP",
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP",
        # v1.4.3 HOTFIX — найдено через GET /_debug/table-schema/leads: в таблице есть
        # 2 orphaned-колонки (is_qualified, is_bad) — boolean NOT NULL без дефолта, которых
        # нет в модели Lead и нет ни одного упоминания в коде (проверено поиском по всему
        # репозиторию) — остатки от более ранней версии схемы. Снимаем блокировку
        # дефолтом вместо добавления в модель — код на них нигде не опирается.
        "ALTER TABLE leads ALTER COLUMN is_qualified SET DEFAULT false",
        "ALTER TABLE leads ALTER COLUMN is_bad SET DEFAULT false",
        # v1.4.4 HOTFIX — та же болезнь для hypotheses: модель Hypothesis давно объявляет
        # 6 полей для ручных/алгоритмических гипотез, которых не было в таблице —
        # POST /suggestions/{id}/action при approve падал с UndefinedColumnError
        # (видно в браузере как "Failed to fetch" из-за отсутствия CORS-заголовков
        # на упавшем процессе).
        "ALTER TABLE hypotheses ADD COLUMN IF NOT EXISTS description TEXT",
        "ALTER TABLE hypotheses ADD COLUMN IF NOT EXISTS change_description TEXT",
        "ALTER TABLE hypotheses ADD COLUMN IF NOT EXISTS forecast TEXT",
        "ALTER TABLE hypotheses ADD COLUMN IF NOT EXISTS object_type VARCHAR(50)",
        "ALTER TABLE hypotheses ADD COLUMN IF NOT EXISTS object_id INTEGER",
        "ALTER TABLE hypotheses ADD COLUMN IF NOT EXISTS source VARCHAR(50)",
        # suggestion_id в БД оказался NOT NULL, хотя в модели Optional — ручные
        # гипотезы без suggestion_id (со страницы /hypotheses) упали бы по той же схеме.
        "ALTER TABLE hypotheses ALTER COLUMN suggestion_id DROP NOT NULL",
        # v1.7.0: универсальный JSON-payload для предложений (create_campaign и
        # будущие типы, которым нужна структура сложнее короткой строки).
        "ALTER TABLE suggestions ADD COLUMN IF NOT EXISTS payload JSON",
        # v1.7.4: запасной уровень атрибуции лида. Если заявка не привязалась к
        # ключевому слову, но кампания известна — она всё равно участвует в
        # анализе. Без этого 73% лидов выпадали, и кампания с реальными
        # заявками выглядела нулевой (разбор на боевых данных gto365).
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS campaign_id INTEGER",
    ]
    for sql in migrations:
        try:
            await conn.execute(text(sql))
            logger.info(f"Migration OK: {sql[:60]}")
        except Exception as e:
            logger.warning(f"Migration skip: {sql[:60]} — {e}")
