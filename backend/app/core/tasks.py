"""
Celery задачи — сбор данных, анализ, трекинг гипотез.
Используем синхронные сессии БД внутри воркера чтобы избежать
конфликтов asyncpg с event loop при prefork.
"""
import asyncio
import logging
from datetime import datetime, timedelta, date
from celery import shared_task
from app.core.celery_app import celery_app

logger = logging.getLogger(__name__)


def run_async(coro):
    """Запустить async функцию из синхронного Celery — каждый раз новый loop"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        # уже внутри async контекста — не должно случаться в воркере
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    else:
        return loop.run_until_complete(coro)


def get_sync_db():
    """Получить синхронную сессию для использования в Celery воркере"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.config import settings

    # Конвертируем asyncpg URL в синхронный psycopg2
    sync_url = settings.DATABASE_URL.replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    engine = create_engine(sync_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine)
    return Session(), engine


@celery_app.task(name="app.core.tasks.collect_and_analyze_all", bind=True, max_retries=3)
def collect_and_analyze_all(self):
    """Запустить сбор + анализ для всех активных кабинетов"""
    session, engine = get_sync_db()
    try:
        from app.models.models import Account
        accounts = session.query(Account).filter(
            Account.is_active == True,
            Account.oauth_token != None
        ).all()
        logger.info(f"Starting collection for {len(accounts)} accounts")
        for account in accounts:
            collect_account_data.delay(account.id)
    finally:
        session.close()
        engine.dispose()


@celery_app.task(name="app.core.tasks.collect_account_data", bind=True, max_retries=3)
def collect_account_data(self, account_id: int):
    """Собрать данные из Директа и Метрики для одного кабинета"""
    import asyncio as _asyncio
    loop = _asyncio.new_event_loop()
    _asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_collect_account_data_async(account_id))
    finally:
        loop.close()
        _asyncio.set_event_loop(None)


async def _collect_account_data_async(account_id: int):
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from app.core.config import settings
    from app.models.models import Account, Campaign, AdGroup, Keyword, KeywordStat
    from app.collectors.direct_collector import YandexDirectCollector
    from app.collectors.metrika_collector import MetrikaCollector
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert

    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with AsyncSessionLocal() as db:
            acc_result = await db.execute(select(Account).where(Account.id == account_id))
            account = acc_result.scalar_one_or_none()
            if not account or not account.oauth_token:
                logger.error(f"Account {account_id} not found or no token")
                return

            date_to = date.today()
            date_from = date_to - timedelta(days=28)

            # ── Сбор из Директа ──────────────────────────────────────────
            async with YandexDirectCollector(account.oauth_token, account.yandex_login) as dc:
                # Кампании
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

                # Группы
                campaign_ids = [str(c["Id"]) for c in campaigns_data]
                if campaign_ids:
                    # Директ API лимит: 10 кампаний за запрос для adgroups
                    groups_data = []
                    for i in range(0, len(campaign_ids), 10):
                        batch = campaign_ids[i:i+10]
                        batch_result = await dc.get_ad_groups(batch)
                        groups_data.extend(batch_result)
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

                    # Ключи
                    # Батчинг ключей по 10 кампаний
                    keywords_data = []
                    for i in range(0, len(campaign_ids), 10):
                        batch = campaign_ids[i:i+10]
                        batch_result = await dc.get_keywords(batch)
                        keywords_data.extend(batch_result)
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

                    # Статистика
                    # Статистику запрашиваем без фильтра по кампаниям — Reports API сам агрегирует
                    stats_data = await dc.get_keyword_stats(date_from, date_to)
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

            # ── Сбор из Метрики ──────────────────────────────────────────
            if account.metrika_counter_id:
                try:
                    async with MetrikaCollector(account.oauth_token, account.metrika_counter_id) as mc:
                        traffic = await mc.get_traffic_summary(date_from, date_to)
                        logger.info(f"Metrika traffic summary for account {account_id}: {traffic}")
                except Exception as e:
                    logger.warning(f"Metrika collection failed for account {account_id}: {e}")

            # Обновить дату синхронизации
            account.last_sync_at = datetime.utcnow()
            await db.commit()
            logger.info(f"Data collection complete for account {account_id}")

        # Запустить анализ
        run_analysis.delay(account_id)

    finally:
        await engine.dispose()


@celery_app.task(name="app.core.tasks.run_analysis", bind=True, max_retries=2)
def run_analysis(self, account_id: int):
    """Запустить CR-анализ и генерацию предложений"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_run_analysis_async(account_id))
    finally:
        loop.close()
        asyncio.set_event_loop(None)


async def _run_analysis_async(account_id: int):
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from app.core.config import settings
    from app.analyzers.cr_analyzer import CRAnalyzer
    from app.generators.suggestion_generator import SuggestionGenerator

    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
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
    finally:
        await engine.dispose()


@celery_app.task(name="app.core.tasks.track_all_hypotheses")
def track_all_hypotheses():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_track_all_hypotheses_async())
    finally:
        loop.close()
        asyncio.set_event_loop(None)


async def _track_all_hypotheses_async():
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from app.core.config import settings
    from app.models.models import Hypothesis
    from sqlalchemy import select, and_

    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
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
    finally:
        await engine.dispose()


@celery_app.task(name="app.core.tasks.track_hypothesis", bind=True)
def track_hypothesis(self, hypothesis_id: int):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_track_hypothesis_async(hypothesis_id))
    finally:
        loop.close()
        asyncio.set_event_loop(None)


async def _track_hypothesis_async(hypothesis_id: int):
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from app.core.config import settings
    from app.models.models import Hypothesis, Suggestion, KeywordStat
    from sqlalchemy import select, func, and_

    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with AsyncSessionLocal() as db:
            h_result = await db.execute(select(Hypothesis).where(Hypothesis.id == hypothesis_id))
            hypothesis = h_result.scalar_one_or_none()
            if not hypothesis:
                return

            now = datetime.utcnow()
            if now < hypothesis.track_until:
                return

            s_result = await db.execute(
                select(Suggestion).where(Suggestion.id == hypothesis.suggestion_id)
            )
            suggestion = s_result.scalar_one_or_none()
            if not suggestion or suggestion.object_type != "keyword":
                return

            keyword_id = suggestion.object_id
            applied_at = hypothesis.applied_at
            before_start = applied_at - timedelta(days=7)
            after_end = applied_at + timedelta(days=7)

            async def get_stats(start, end):
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
                return {"clicks": int(row.clicks or 0), "spend": float(row.spend or 0)}

            before_stats = await get_stats(before_start, applied_at)
            after_stats = await get_stats(applied_at, after_end)

            hypothesis.metrics_before = before_stats
            hypothesis.metrics_after = after_stats

            if before_stats["clicks"] < 10 or after_stats["clicks"] < 10:
                hypothesis.verdict = "insufficient"
                hypothesis.report = "Недостаточно кликов для статистически значимого вывода."
            else:
                cr_before = before_stats["clicks"]
                cr_after = after_stats["clicks"]
                delta = (cr_after - cr_before) / cr_before * 100
                hypothesis.delta_percent = round(delta, 2)
                if delta >= 10:
                    hypothesis.verdict = "confirmed"
                    hypothesis.report = f"Гипотеза подтверждена. Трафик вырос на {delta:.1f}%."
                elif delta <= -10:
                    hypothesis.verdict = "rejected"
                    hypothesis.report = f"Гипотеза отклонена. Трафик упал на {abs(delta):.1f}%."
                else:
                    hypothesis.verdict = "neutral"
                    hypothesis.report = f"Изменение нейтральное ({delta:+.1f}%). Продолжить наблюдение."

            await db.commit()
            logger.info(f"Hypothesis {hypothesis_id} tracked: {hypothesis.verdict}")
    finally:
        await engine.dispose()
