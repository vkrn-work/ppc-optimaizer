"""
Celery задачи — сбор данных, анализ, трекинг гипотез.
"""
import asyncio
import logging
from datetime import datetime, timedelta, date
from celery import shared_task
from app.core.celery_app import celery_app

logger = logging.getLogger(__name__)


def run_async(coro):
    """Запустить async функцию из синхронного Celery"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="app.core.tasks.collect_and_analyze_all", bind=True, max_retries=3)
def collect_and_analyze_all(self):
    """Запустить сбор + анализ для всех активных кабинетов"""
    return run_async(_collect_and_analyze_all())


async def _collect_and_analyze_all():
    from app.db.database import AsyncSessionLocal
    from app.models.models import Account
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Account).where(Account.is_active == True, Account.oauth_token != None)
        )
        accounts = result.scalars().all()
        logger.info(f"Starting collection for {len(accounts)} accounts")

        for account in accounts:
            collect_account_data.delay(account.id)


@celery_app.task(name="app.core.tasks.collect_account_data", bind=True, max_retries=3)
def collect_account_data(self, account_id: int):
    """Собрать данные из Директа и Метрики для одного кабинета"""
    return run_async(_collect_account_data(account_id))


async def _collect_account_data(account_id: int):
    from app.db.database import AsyncSessionLocal
    from app.models.models import Account, Campaign, AdGroup, Keyword, KeywordStat
    from app.collectors.direct_collector import YandexDirectCollector
    from app.collectors.metrika_collector import MetrikaCollector
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert

    async with AsyncSessionLocal() as db:
        # Загрузить аккаунт
        acc_result = await db.execute(select(Account).where(Account.id == account_id))
        account = acc_result.scalar_one_or_none()
        if not account or not account.oauth_token:
            logger.error(f"Account {account_id} not found or no token")
            return

        date_to = date.today()
        date_from = date_to - timedelta(days=28)

        async with YandexDirectCollector(account.oauth_token, account.yandex_login) as dc:
            # 1. Кампании
            campaigns_data = await dc.get_campaigns()
            for c in campaigns_data:
                stmt = insert(Campaign).values(
                    account_id=account_id,
                    direct_id=str(c["Id"]),
                    name=c.get("Name", ""),
                    campaign_type=c.get("Type", ""),
                    status=c.get("Status", ""),
                    is_active=c.get("Status") == "ON",
                ).on_conflict_do_update(
                    index_elements=["account_id", "direct_id"],
                    set_={"name": c.get("Name", ""), "status": c.get("Status", "")},
                )
                await db.execute(stmt)
            await db.commit()

            # 2. Группы объявлений
            campaign_ids = [str(c["Id"]) for c in campaigns_data]
            if campaign_ids:
                groups_data = await dc.get_ad_groups(campaign_ids[:100])
                for g in groups_data:
                    camp_result = await db.execute(
                        select(Campaign).where(
                            Campaign.account_id == account_id,
                            Campaign.direct_id == str(g["CampaignId"]),
                        )
                    )
                    camp = camp_result.scalar_one_or_none()
                    if not camp:
                        continue
                    stmt = insert(AdGroup).values(
                        account_id=account_id,
                        campaign_id=camp.id,
                        direct_id=str(g["Id"]),
                        name=g.get("Name", ""),
                        status=g.get("Status", ""),
                    ).on_conflict_do_update(
                        index_elements=["account_id", "direct_id"],
                        set_={"name": g.get("Name", ""), "status": g.get("Status", "")},
                    )
                    await db.execute(stmt)
                await db.commit()

            # 3. Ключевые слова
            keywords_data = await dc.get_keywords(campaign_ids[:100])
            for kw in keywords_data:
                group_result = await db.execute(
                    select(AdGroup).where(
                        AdGroup.account_id == account_id,
                        AdGroup.direct_id == str(kw["AdGroupId"]),
                    )
                )
                group = group_result.scalar_one_or_none()
                if not group:
                    continue
                bid = kw.get("Bid")
                stmt = insert(Keyword).values(
                    account_id=account_id,
                    ad_group_id=group.id,
                    direct_id=str(kw["Id"]),
                    phrase=kw.get("Keyword", ""),
                    current_bid=float(bid) / 1_000_000 if bid else None,
                    status=kw.get("Status", "ACTIVE"),
                ).on_conflict_do_update(
                    index_elements=["account_id", "direct_id"],
                    set_={
                        "phrase": kw.get("Keyword", ""),
                        "current_bid": float(bid) / 1_000_000 if bid else None,
                        "status": kw.get("Status", "ACTIVE"),
                    },
                )
                await db.execute(stmt)
            await db.commit()

            # 4. Статистика по ключам
            stats_data = await dc.get_keyword_stats(date_from, date_to, campaign_ids[:100])
            for row in stats_data:
                kw_result = await db.execute(
                    select(Keyword).where(
                        Keyword.account_id == account_id,
                        Keyword.direct_id == str(row.get("CriterionId", "")),
                    )
                )
                kw = kw_result.scalar_one_or_none()
                if not kw:
                    continue
                try:
                    stat_date = datetime.strptime(row["Date"], "%Y-%m-%d")
                    clicks = int(row.get("Clicks", 0))
                    spend = float(row.get("Cost", 0))
                    if clicks == 0 and spend == 0:
                        continue
                    stmt = insert(KeywordStat).values(
                        account_id=account_id,
                        keyword_id=kw.id,
                        date=stat_date,
                        impressions=int(row.get("Impressions", 0)),
                        clicks=clicks,
                        spend=spend,
                        avg_cpc=float(row.get("AvgCpc", 0)) or None,
                        avg_bid=float(row.get("AvgEffectiveBid", 0)) or None,
                        traffic_volume=int(row.get("AvgTrafficVolume", 0)) or None,
                        avg_position=float(row.get("AvgImpressionPosition", 0)) or None,
                    ).on_conflict_do_update(
                        index_elements=["account_id", "keyword_id", "date"],
                        set_={"clicks": clicks, "spend": spend},
                    )
                    await db.execute(stmt)
                except (ValueError, KeyError) as e:
                    logger.warning(f"Error parsing stat row: {e}")
            await db.commit()

        # Обновить дату последней синхронизации
        account.last_sync_at = datetime.utcnow()
        await db.commit()
        logger.info(f"Data collection complete for account {account_id}")

        # Запустить анализ
        run_analysis.delay(account_id)


@celery_app.task(name="app.core.tasks.run_analysis", bind=True, max_retries=2)
def run_analysis(self, account_id: int):
    """Запустить CR-анализ и генерацию предложений"""
    return run_async(_run_analysis(account_id))


async def _run_analysis(account_id: int):
    from app.db.database import AsyncSessionLocal
    from app.analyzers.cr_analyzer import CRAnalyzer
    from app.generators.suggestion_generator import SuggestionGenerator

    async with AsyncSessionLocal() as db:
        analyzer = CRAnalyzer(db, account_id)
        analysis = await analyzer.run_full_analysis()

        generator = SuggestionGenerator(db, account_id)
        suggestions = await generator.generate_for_analysis(analysis)
        scale_suggestions = await generator.generate_scale_suggestions(analysis)

        logger.info(
            f"Analysis complete for account {account_id}: "
            f"{len(suggestions) + len(scale_suggestions)} suggestions generated"
        )
        return analysis.id


@celery_app.task(name="app.core.tasks.track_all_hypotheses")
def track_all_hypotheses():
    """Ежедневный трекинг всех активных гипотез"""
    return run_async(_track_all_hypotheses())


async def _track_all_hypotheses():
    from app.db.database import AsyncSessionLocal
    from app.models.models import Hypothesis
    from sqlalchemy import select, and_

    async with AsyncSessionLocal() as db:
        now = datetime.utcnow()
        result = await db.execute(
            select(Hypothesis).where(
                and_(
                    Hypothesis.track_until >= now,
                    Hypothesis.verdict == None,
                )
            )
        )
        hypotheses = result.scalars().all()
        logger.info(f"Tracking {len(hypotheses)} hypotheses")
        for h in hypotheses:
            track_hypothesis.delay(h.id)


@celery_app.task(name="app.core.tasks.track_hypothesis", bind=True)
def track_hypothesis(self, hypothesis_id: int):
    return run_async(_track_hypothesis(hypothesis_id))


async def _track_hypothesis(hypothesis_id: int):
    """7-дневный трекинг гипотезы — сравниваем метрики до и после"""
    from app.db.database import AsyncSessionLocal
    from app.models.models import Hypothesis, Suggestion, KeywordStat, Keyword
    from sqlalchemy import select, func, and_

    async with AsyncSessionLocal() as db:
        h_result = await db.execute(select(Hypothesis).where(Hypothesis.id == hypothesis_id))
        hypothesis = h_result.scalar_one_or_none()
        if not hypothesis:
            return

        now = datetime.utcnow()
        if now < hypothesis.track_until:
            # Ещё не время для финального вердикта
            return

        # Получить связанное предложение
        s_result = await db.execute(
            select(Suggestion).where(Suggestion.id == hypothesis.suggestion_id)
        )
        suggestion = s_result.scalar_one_or_none()
        if not suggestion or suggestion.object_type != "keyword":
            return

        keyword_id = suggestion.object_id
        applied_at = hypothesis.applied_at

        # Метрики ДО (7 дней до применения)
        before_end = applied_at
        before_start = applied_at - timedelta(days=7)
        before_stats = await _get_keyword_period_stats(db, keyword_id, before_start, before_end)

        # Метрики ПОСЛЕ (7 дней после применения)
        after_start = applied_at
        after_end = applied_at + timedelta(days=7)
        after_stats = await _get_keyword_period_stats(db, keyword_id, after_start, after_end)

        hypothesis.metrics_before = before_stats
        hypothesis.metrics_after = after_stats

        # Рассчитать дельту по CR
        cr_before = before_stats.get("cr_click_lead", 0)
        cr_after = after_stats.get("cr_click_lead", 0)

        if before_stats.get("clicks", 0) < 10 or after_stats.get("clicks", 0) < 10:
            hypothesis.verdict = "insufficient"
            hypothesis.report = "Недостаточно кликов за период для статистически значимого вывода."
        elif cr_before > 0:
            delta = (cr_after - cr_before) / cr_before * 100
            hypothesis.delta_percent = round(delta, 2)
            if delta >= 10:
                hypothesis.verdict = "confirmed"
                hypothesis.report = (
                    f"Гипотеза подтверждена. CR вырос с {cr_before*100:.1f}% до {cr_after*100:.1f}% "
                    f"(+{delta:.1f}%). Изменение эффективно."
                )
            elif delta <= -10:
                hypothesis.verdict = "rejected"
                hypothesis.report = (
                    f"Гипотеза отклонена. CR упал с {cr_before*100:.1f}% до {cr_after*100:.1f}% "
                    f"({delta:.1f}%). Рекомендуется откат изменения."
                )
            else:
                hypothesis.verdict = "neutral"
                hypothesis.report = (
                    f"Изменение нейтральное. CR: {cr_before*100:.1f}% → {cr_after*100:.1f}% "
                    f"({delta:+.1f}%). Продолжить наблюдение."
                )
        else:
            hypothesis.verdict = "insufficient"
            hypothesis.report = "CR до изменения = 0. Нет базы для сравнения."

        await db.commit()
        logger.info(f"Hypothesis {hypothesis_id} tracked: {hypothesis.verdict}")


async def _get_keyword_period_stats(db, keyword_id: int, start: datetime, end: datetime) -> dict:
    from sqlalchemy import select, func, and_
    from app.models.models import KeywordStat

    result = await db.execute(
        select(
            func.sum(KeywordStat.clicks).label("clicks"),
            func.sum(KeywordStat.spend).label("spend"),
        ).where(
            and_(
                KeywordStat.keyword_id == keyword_id,
                KeywordStat.date >= start,
                KeywordStat.date <= end,
            )
        )
    )
    row = result.one()
    clicks = int(row.clicks or 0)
    spend = float(row.spend or 0)
    return {"clicks": clicks, "spend": spend, "cr_click_lead": 0}
