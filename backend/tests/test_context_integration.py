"""Интеграционный тест разноски заявок и контекста для LLM.

Поднимает всю схему в SQLite in-memory — БД не нужна. Запуск:
    cd backend && python -m pytest tests/ -q

Сценарий ровно тот, что ломался на боевых данных:
  * CRM-выгрузка загружена ДО синхронизации Директа — keyword_id не проставлен;
  * один поисковый запрос откручивался по двум ключам (2 клика против 40);
  * у заявки utm_campaign не совпадает с именем кампании в Директе.
"""
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker


# SQLite не знает JSONB (Account.analysis_config) — для теста рендерим как JSON.
@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


from app.models.models import (  # noqa: E402
    Base, Account, Campaign, AdGroup, Keyword, KeywordStat, SearchQuery, Lead, LeadStatus,
)
from app.importers.lead_attribution import reattribute_account  # noqa: E402
from app.analyzers.llm_context import build_context  # noqa: E402

NOW = datetime.utcnow()


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        await _seed(session)
        yield session
    await engine.dispose()


async def _seed(db: AsyncSession):
    db.add(Account(id=1, name="acc", yandex_login="l", oauth_token="t"))
    db.add(Campaign(id=100, account_id=1, direct_id="c1", campaign_type="TEXT_CAMPAIGN",
                    name="Спецстали_Quard_все /gto365.ru /РФ3", strategy_type="AUTO", status="ON"))
    db.add(Campaign(id=200, account_id=1, direct_id="c2", campaign_type="TEXT_CAMPAIGN",
                    name="Hardox /gto365.ru", strategy_type="MANUAL_CPC", status="ON"))
    db.add(AdGroup(id=10, account_id=1, campaign_id=100, direct_id="g1", name="g1", status="ACCEPTED"))
    db.add(AdGroup(id=20, account_id=1, campaign_id=200, direct_id="g2", name="g2", status="ACCEPTED"))
    db.add(Keyword(id=1, account_id=1, ad_group_id=10, direct_id="k1", phrase="квард сталь"))
    db.add(Keyword(id=2, account_id=1, ad_group_id=20, direct_id="k2",
                   phrase="Износостойкая сталь +Хардокс -Аналоги"))
    for kw_id, spend, clicks in ((1, 5000, 50), (2, 30000, 120)):
        db.add(KeywordStat(account_id=1, keyword_id=kw_id, date=NOW - timedelta(days=3),
                           impressions=1000, clicks=clicks, spend=spend))
    # Один и тот же запрос показывался по двум ключам. Побеждать должен тот,
    # по которому он реально откручивался (40 кликов), а не произвольный.
    db.add(SearchQuery(account_id=1, keyword_id=1, date=NOW - timedelta(days=3),
                       query="цена на хардокс", keyword_phrase="квард сталь",
                       clicks=2, impressions=10))
    db.add(SearchQuery(account_id=1, keyword_id=2, date=NOW - timedelta(days=3),
                       query="цена на хардокс",
                       keyword_phrase="Износостойкая сталь +Хардокс -Аналоги",
                       clicks=40, impressions=300))
    await db.commit()

    # Заявки без разноски — как если бы файл загрузили до синхронизации Директа.
    db.add(Lead(account_id=1, status=LeadStatus.lead, raw_status="Не прошло КП",
                is_mql=True, is_sql=True, utm_term="цена на хардокс",
                utm_campaign="Кампания переименована", created_at=NOW - timedelta(days=2)))
    db.add(Lead(account_id=1, status=LeadStatus.lead, raw_status="В работе",
                is_mql=True, is_sql=False, utm_campaign="Спецстали-Quard-все",
                created_at=NOW - timedelta(days=2)))
    db.add(Lead(account_id=1, status=LeadStatus.lead, raw_status="Новая",
                is_mql=True, is_sql=False, utm_term="что-то другое",
                created_at=NOW - timedelta(days=1)))
    await db.commit()


DATASET = [
    {"keyword_id": 1, "phrase": "квард сталь", "clicks": 50,
     "spend_rub": 5000.0, "thin_data": False},
    {"keyword_id": 2, "phrase": "Износостойкая сталь +Хардокс -Аналоги", "clicks": 120,
     "spend_rub": 30000.0, "thin_data": False,
     "crm_leads": 1, "crm_mql": 1, "crm_sql": 1},
]


@pytest.mark.asyncio
async def test_reattribution_recovers_leads_imported_before_sync(db):
    stats = await reattribute_account(db, 1)

    assert stats["total"] == 3
    assert stats["gained_keyword"] == 1
    assert stats["gained_campaign"] == 2
    assert stats["still_unmatched"] == 1

    leads = {l.raw_status: l for l in (await db.execute(select(Lead))).scalars().all()}

    # Запрос отдан ключу с 40 кликами, а не ключу с 2 (в v1.7.4 побеждал произвольный).
    assert leads["Не прошло КП"].keyword_id == 2
    # campaign_id выведен из ключа, хотя utm_campaign не совпадает с именем в Директе.
    assert leads["Не прошло КП"].campaign_id == 200
    assert leads["Не прошло КП"].matched_by == "search_query"

    # Мягкий матчинг имени кампании: «Спецстали-Quard-все» → «Спецстали_Quard_все /...».
    assert leads["В работе"].campaign_id == 100
    assert leads["В работе"].matched_by == "campaign"

    assert leads["Новая"].campaign_id is None


@pytest.mark.asyncio
async def test_context_funnel_does_not_mix_levels(db):
    ctx = await build_context(db, 1, DATASET, period_days=28)
    totals = ctx["account_totals"]

    # Все три уровня воронки — из CRM целиком.
    assert (totals["crm_leads"], totals["crm_mql"], totals["crm_sql"]) == (3, 3, 1)
    # Keyword-уровень отдаётся отдельно и не подменяет собой итоги.
    assert totals["crm_leads_by_keyword"] == 1
    # CR считаются внутри одного уровня: в v1.7.4 crm_mql был keyword-уровневым,
    # а crm_leads — общим, и CR lead→MQL выходил ~33% вместо 100%.
    assert totals["cr_lead_to_mql_pct"] == 100.0
    assert totals["cr_mql_to_sql_pct"] == 33.3


@pytest.mark.asyncio
async def test_context_campaigns_carry_real_leads(db):
    ctx = await build_context(db, 1, DATASET, period_days=28)
    by_name = {c["campaign"]: c for c in ctx["campaigns"]}

    hardox = by_name["Hardox /gto365.ru"]
    assert (hardox["crm_leads"], hardox["crm_sql"]) == (1, 1)
    assert hardox["cost_per_sql_rub"] == 30000.0

    quard = by_name["Спецстали_Quard_все /gto365.ru /РФ3"]
    assert quard["crm_leads"] == 1
    # 0 SQL → null, а не 0₽. Раньше деление отдавало 0.0 и модель читала это
    # как «заявки достаются бесплатно».
    assert quard["cost_per_sql_rub"] is None


@pytest.mark.asyncio
async def test_context_reports_attribution_coverage(db):
    ctx = await build_context(db, 1, DATASET, period_days=28)
    attr = ctx["attribution_quality"]

    assert attr["leads_total_in_crm"] == 3
    assert attr["leads_keyword_coverage_pct"] == 33.3
    assert attr["sql_keyword_coverage_pct"] == 100.0
    # Размеры справочников: если search_queries=0, низкое покрытие объясняется
    # несобранными данными Директа, а не отсутствием заявок.
    assert attr["reattribution_run"]["matchers"]["search_queries"] == 1


@pytest.mark.asyncio
async def test_context_reports_data_freshness(db):
    ctx = await build_context(db, 1, DATASET, period_days=28)
    fresh = ctx["data_freshness"]

    assert fresh["last_direct_stat_date"] is not None
    assert fresh["last_crm_lead_date"] is not None
    assert isinstance(fresh["crm_lag_days"], int)
