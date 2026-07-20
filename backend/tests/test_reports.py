"""Тесты конструктора отчётов — подсчёт заявок в разных разрезах.

Воспроизводят боевой случай: отчёт «По кампаниям» за месяц показывал 1 заявку
на 64 269 руб расхода, потому что считал только по Lead.keyword_id и не видел
заявок, привязанных к кампании.

Запуск: cd backend && python -m pytest tests/ -q
"""
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


from app.models.models import (  # noqa: E402
    Base, Account, Campaign, AdGroup, Keyword, KeywordStat, Lead, LeadStatus,
)
from app.api.routes.reports import get_report  # noqa: E402

NOW = datetime.utcnow()


async def _report(db, **kwargs):
    """Вызов эндпоинта напрямую: все Query-дефолты передаём явно, иначе в
    функцию приедут объекты Query вместо значений."""
    params = dict(group_by="campaign", period="month", date_from=None, date_to=None,
                  campaign_id=None, ad_group_id=None, active_only=False, limit=1000)
    params.update(kwargs)
    return await get_report(1, db=db, **params)


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
                    name="Спецстали_Quard_все /gto365.ru /РФ3", strategy_type="AUTO",
                    status="ON", is_active=True))
    db.add(Campaign(id=200, account_id=1, direct_id="c2", campaign_type="TEXT_CAMPAIGN",
                    name="Спецстали_Hardox /gto365.ru /РФ5", strategy_type="AUTO",
                    status="ON", is_active=True))
    db.add(AdGroup(id=10, account_id=1, campaign_id=100, direct_id="g1", name="g1", status="ACCEPTED"))
    db.add(AdGroup(id=20, account_id=1, campaign_id=200, direct_id="g2", name="g2", status="ACCEPTED"))
    db.add(Keyword(id=1, account_id=1, ad_group_id=10, direct_id="k1", phrase="квард"))
    db.add(Keyword(id=2, account_id=1, ad_group_id=20, direct_id="k2", phrase="хардокс"))
    for kw_id, spend, clicks in ((1, 23173, 72), (2, 21831, 59)):
        db.add(KeywordStat(account_id=1, keyword_id=kw_id, date=NOW - timedelta(days=5),
                           impressions=1000, clicks=clicks, spend=spend))

    # 1 заявка привязана к ключу (кампания 100)
    db.add(Lead(account_id=1, status=LeadStatus.sql, raw_status="Запущен БП",
                is_mql=True, is_sql=True, keyword_id=1, campaign_id=100,
                matched_by="search_query", created_at=NOW - timedelta(days=4)))
    # 5 заявок привязаны только к кампании 200 — раньше они были невидимы
    for _ in range(5):
        db.add(Lead(account_id=1, status=LeadStatus.sql, raw_status="Запущен БП",
                    is_mql=True, is_sql=True, campaign_id=200, matched_by="campaign",
                    created_at=NOW - timedelta(days=3)))
    # 2 заявки без разноски вообще
    for _ in range(2):
        db.add(Lead(account_id=1, status=LeadStatus.lead, raw_status="Новая",
                    is_mql=True, is_sql=False, created_at=NOW - timedelta(days=2)))
    await db.commit()


@pytest.mark.asyncio
async def test_campaign_report_counts_campaign_level_leads(db):
    """ГЛАВНЫЙ ФИКС. До v1.7.5 у Hardox было leads=0, sql=0, cpl=None."""
    r = await _report(db, group_by="campaign")
    by_name = {row["campaign_name"]: row for row in r["rows"]}

    hardox = by_name["Спецстали_Hardox /gto365.ru /РФ5"]
    assert hardox["leads"] == 5
    assert hardox["sql"] == 5
    assert hardox["cpl"] == 4366.2

    quard = by_name["Спецстали_Quard_все /gto365.ru /РФ3"]
    assert quard["leads"] == 1


@pytest.mark.asyncio
async def test_campaign_report_totals_and_remainder(db):
    r = await _report(db, group_by="campaign")

    assert r["totals"]["leads"] == 6
    assert r["totals"]["sql"] == 6
    # Старая версия давала бы 45004 / 1 = 45004 руб за заявку.
    assert r["totals"]["cpl"] == 7500.67

    attr = r["attribution"]
    assert attr["leads_total_crm"] == 8
    assert attr["leads_in_report"] == 6
    # Две неразнесённые заявки видны явно, а не растворяются в нулях.
    assert attr["leads_unattributed"] == 2
    assert attr["filtered"] is False


@pytest.mark.asyncio
async def test_keyword_report_keeps_keyword_level_only(db):
    """В разрезе по ключам заявка уровня кампании не приписывается ключу
    наугад — она честно уходит в остаток."""
    r = await _report(db, group_by="keyword")
    by_phrase = {row["keyword_phrase"]: row for row in r["rows"]}

    assert by_phrase["квард"]["leads"] == 1
    assert by_phrase["хардокс"]["leads"] == 0
    assert r["attribution"]["leads_unattributed"] == 7


@pytest.mark.asyncio
async def test_date_report_loses_nothing(db):
    """По дням должны сойтись ВСЕ заявки: дата есть у каждой.

    Заявки здесь пришли в дни без показов — раньше такие строки не
    создавались вовсе и заявки исчезали целиком.
    """
    r = await _report(db, group_by="date")
    assert r["totals"]["leads"] == 8
    assert r["attribution"]["leads_unattributed"] == 0
    # Строки без открутки помечены флагом, а не выглядят как обычные.
    assert any(row["no_spend_in_period"] for row in r["rows"])


@pytest.mark.asyncio
async def test_remainder_not_computed_when_filtered(db):
    """При фильтре остаток не считается: часть заявок относится к
    отфильтрованным кампаниям, и разница выглядела бы как потеря."""
    r = await _report(db, group_by="campaign", campaign_id=200)
    assert r["attribution"]["filtered"] is True
    assert r["attribution"]["leads_unattributed"] is None
