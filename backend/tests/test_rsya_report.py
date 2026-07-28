"""v1.7.6: сквозной тест на данных из реальной выгрузки gto365 (orders 65.xlsx).

Проверяет то, что расходилось с Роистатом за неделю 2026-07-13..19:
  Роистат: 8 заявок (Поиск 5, РСЯ 3), расход 27 400 (Поиск 21 400 + РСЯ 6 000).
  Приложение до фикса: 4 заявки, только Поиск, РСЯ не видно вовсе.

Здесь: РСЯ-кампания без единого ключа, но с расходом в campaign_stats и с
заявками по campaign_id — должна появиться в отчёте с расходом и заявками.
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
    Base, Account, Campaign, AdGroup, Keyword, KeywordStat, CampaignStat,
    Lead, LeadStatus,
)
from app.importers.lead_attribution import reattribute_account  # noqa: E402
from app.api.routes.reports import get_report, get_report_tree  # noqa: E402

NOW = datetime.utcnow() - timedelta(days=1)

CH_QUARD = "ГТО 4 → Поиск → Спецстали_Quard_все /gto365.ru /РФ3 → ! Quard → 17223320102 → квард"
CH_HARDOX = "ГТО 4 → Поиск → Спецстали_Hardox /gto365.ru /РФ5 → Hardox общие → объявление → хардокс"
CH_RSYA = "ГТО 4 → РСЯ → Спецстали /gto365.ru /ретаргетинг /РФ + → hard-met ретаргетинг → 17183690698"


async def _report(db, **kw):
    params = dict(group_by="campaign", period="week", date_from=None, date_to=None,
                  campaign_id=None, ad_group_id=None, active_only=False, limit=1000)
    params.update(kw)
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
    db.add(Campaign(id=300, account_id=1, direct_id="c3", campaign_type="TEXT_CAMPAIGN",
                    name="Спецстали /gto365.ru /ретаргетинг /РФ +", strategy_type="AUTO",
                    status="ON", is_active=True))
    db.add(AdGroup(id=10, account_id=1, campaign_id=100, direct_id="g1", name="! Quard", status="ACCEPTED"))
    db.add(AdGroup(id=20, account_id=1, campaign_id=200, direct_id="g2", name="Hardox общие", status="ACCEPTED"))
    db.add(Keyword(id=1, account_id=1, ad_group_id=10, direct_id="k1", phrase="квард"))
    db.add(Keyword(id=2, account_id=1, ad_group_id=20, direct_id="k2", phrase="хардокс"))
    db.add(KeywordStat(account_id=1, keyword_id=1, date=NOW - timedelta(days=1),
                       impressions=463, clicks=29, spend=9715))
    db.add(KeywordStat(account_id=1, keyword_id=2, date=NOW - timedelta(days=1),
                       impressions=240, clicks=13, spend=5734))
    db.add(CampaignStat(account_id=1, campaign_id=100, date=NOW - timedelta(days=1),
                        impressions=463, clicks=29, spend=9715))
    db.add(CampaignStat(account_id=1, campaign_id=200, date=NOW - timedelta(days=1),
                        impressions=240, clicks=13, spend=5734))
    db.add(CampaignStat(account_id=1, campaign_id=300, date=NOW - timedelta(days=1),
                        impressions=1200, clicks=109, spend=6000))
    await db.commit()

    def lead(chan_chain, status, when):
        return Lead(account_id=1, status=LeadStatus.lead, raw_status=status,
                    is_mql=True, is_sql="КП" in status or "БП" in status,
                    source_raw=chan_chain, created_at=when)
    db.add(lead(CH_QUARD, "КП", NOW))
    db.add(lead(CH_QUARD, "Запущен БП", NOW - timedelta(days=2)))
    db.add(lead(CH_QUARD, "КП", NOW - timedelta(days=3)))
    db.add(lead(CH_HARDOX, "КП", NOW - timedelta(days=2)))
    db.add(lead(CH_HARDOX, "КП", NOW - timedelta(days=3)))
    db.add(lead(CH_RSYA, "Обработка лида", NOW))
    db.add(lead(CH_RSYA, "КП", NOW))
    db.add(lead(CH_RSYA, "Не наша номенклатура", NOW - timedelta(days=3)))  # junk → не MQL
    await db.commit()

    await reattribute_account(db, 1)


@pytest.mark.asyncio
async def test_rsya_leads_attributed_by_chain(db):
    """РСЯ-заявки получают campaign_id из цепочки, хотя ключей у кампании нет."""
    from sqlalchemy import select
    rsya = (await db.execute(
        select(Lead).where(Lead.raw_status.in_(["Обработка лида", "Не наша номенклатура"]))
    )).scalars().all()
    for l in rsya:
        assert l.campaign_id == 300
    quard = (await db.execute(select(Lead).where(Lead.source_raw == CH_QUARD))).scalars().all()
    assert all(l.campaign_id == 100 for l in quard)


@pytest.mark.asyncio
async def test_report_shows_rsya_row_and_spend(db):
    r = await _report(db)
    by_name = {row["campaign_name"]: row for row in r["rows"]}

    assert "Спецстали /gto365.ru /ретаргетинг /РФ +" in by_name
    rsya = by_name["Спецстали /gto365.ru /ретаргетинг /РФ +"]
    assert rsya["spend"] == 6000.0
    assert rsya["leads"] == 3
    assert rsya["no_keywords"] is True

    assert r["totals"]["spend"] == 21449.0
    assert r["attribution"]["campaign_spend_source"] == "campaign_stats"


@pytest.mark.asyncio
async def test_report_total_leads_matches_crm(db):
    r = await _report(db)
    assert r["totals"]["leads"] == 8
    assert r["attribution"]["leads_total_crm"] == 8
    assert r["attribution"]["leads_unattributed"] == 0


@pytest.mark.asyncio
async def test_tree_nesting(db):
    t = await get_report_tree(1, db=db, period="week", date_from=None, date_to=None, active_only=False)
    by_name = {c["campaign_name"]: c for c in t["campaigns"]}
    quard = by_name["Спецстали_Quard_все /gto365.ru /РФ3"]
    assert quard["groups"][0]["ad_group_name"] == "! Quard"
    assert quard["groups"][0]["keywords"][0]["keyword_phrase"] == "квард"
    assert quard["leads"] == 3
    assert by_name["Спецстали /gto365.ru /ретаргетинг /РФ +"]["leads"] == 3
