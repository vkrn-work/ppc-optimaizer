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
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    else:
        return loop.run_until_complete(coro)


def get_sync_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.config import settings

    sync_url = settings.DATABASE_URL.replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    engine = create_engine(sync_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine)
    return Session(), engine


@celery_app.task(name="app.core.tasks.collect_and_analyze_all", bind=True, max_retries=3)
def collect_and_analyze_all(self):
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
def collect_account_data(self, account_id: int, days: int = 28):
    """
    Собрать данные из Директа и Метрики для одного кабинета.
    days: за сколько дней собирать статистику (28 по умолчанию, 90 для истории).
    """
    import asyncio as _asyncio
    loop = _asyncio.new_event_loop()
    _asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_collect_account_data_async(account_id, days=days))
    finally:
        loop.close()
        _asyncio.set_event_loop(None)


async def _collect_account_data_async(account_id: int, days: int = 28):
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
            date_from = date_to - timedelta(days=days)
            logger.info(f"Collecting account {account_id} for {days} days: {date_from} — {date_to}")

            # ── Сбор из Директа ──────────────────────────────────────────
            async with YandexDirectCollector(account.oauth_token, account.yandex_login) as dc:
                campaigns_data = await dc.get_campaigns()
                logger.info(f"Campaigns from Yandex Direct: {len(campaigns_data)}")

                for c in campaigns_data:
                    # ВАЖНО: запрос фильтрует States:ON + Statuses:ACCEPTED,
                    # значит все пришедшие кампании уже активны.
                    # is_active=c.get("Status")=="ON" — это БАГ (Status != State).
                    strategy = c.get("_strategy", "UNKNOWN")
                    stmt = insert(Campaign).values(
                        account_id=account_id,
                        direct_id=str(c["Id"]),
                        name=c.get("Name", ""),
                        campaign_type=c.get("Type", ""),
                        status=c.get("Status", ""),
                        strategy_type=strategy,
                        is_active=True,  # все из этого запроса активны
                    ).on_conflict_do_update(
                        index_elements=["account_id", "direct_id"],
                        set_={
                            "name": c.get("Name", ""),
                            "status": c.get("Status", ""),
                            "strategy_type": strategy,
                            "is_active": True,
                        },
                    )
                    await db.execute(stmt)
                await db.commit()

                campaign_ids = [str(c["Id"]) for c in campaigns_data]
                if campaign_ids:
                    # Группы
                    groups_data = []
                    for i in range(0, len(campaign_ids), 10):
                        batch = campaign_ids[i:i+10]
                        batch_result = await dc.get_ad_groups(batch)
                        groups_data.extend(batch_result)
                    logger.info(f"AdGroups: {len(groups_data)}")
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
                    keywords_data = []
                    for i in range(0, len(campaign_ids), 10):
                        batch = campaign_ids[i:i+10]
                        batch_result = await dc.get_keywords(batch)
                        keywords_data.extend(batch_result)
                    logger.info(f"Keywords: {len(keywords_data)}")
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
                        # Bid приходит в микрорублях (1 руб = 1_000_000 микрорублей)
                        bid_rub = float(bid) / 1_000_000 if bid and float(bid) > 0 else None
                        stmt = insert(Keyword).values(
                            account_id=account_id,
                            ad_group_id=group.id,
                            direct_id=str(kw["Id"]),
                            phrase=kw.get("Keyword", ""),
                            current_bid=bid_rub,
                            status=kw.get("Status", "ACTIVE"),
                        ).on_conflict_do_update(
                            index_elements=["account_id", "direct_id"],
                            set_={
                                "phrase": kw.get("Keyword", ""),
                                "current_bid": bid_rub,
                                "status": kw.get("Status", "ACTIVE"),
                            },
                        )
                        await db.execute(stmt)
                    await db.commit()

                    # Объявления
                    for i in range(0, len(campaign_ids), 10):
                        batch = campaign_ids[i:i+10]
                        try:
                            await dc.get_ads(batch)
                        except Exception as e:
                            logger.warning(f"Ads collection error: {e}")

                    # Статистика по ключам
                    stats_data = await dc.get_keyword_stats(date_from, date_to)
                    logger.info(f"Keyword stats rows: {len(stats_data)}")
                    saved_stats = 0
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
                            clicks = int(float(row.get("Clicks", 0) or 0))
                            spend = float(row.get("Cost", 0) or 0)
                            impressions = int(float(row.get("Impressions", 0) or 0))
                            if clicks == 0 and impressions == 0:
                                continue

                            def safe_float(v):
                                try:
                                    r = float(v)
                                    return r if r > 0 else None
                                except:
                                    return None

                            def safe_int(v):
                                try:
                                    r = int(float(v))
                                    return r if r > 0 else None
                                except:
                                    return None

                            # CTR приходит в процентах ("5.23")
                            ctr_val = safe_float(row.get("Ctr"))
                            # AvgEffectiveBid приходит в микрорублях — конвертируем
                            avg_bid_raw = safe_float(row.get("AvgEffectiveBid"))
                            avg_bid_rub = avg_bid_raw / 1_000_000 if avg_bid_raw else None
                            # AvgCpc уже в рублях
                            avg_cpc_val = safe_float(row.get("AvgCpc"))

                            stmt = insert(KeywordStat).values(
                                account_id=account_id,
                                keyword_id=kw.id,
                                date=stat_date,
                                impressions=impressions,
                                clicks=clicks,
                                spend=spend,
                                avg_cpc=avg_cpc_val,
                                avg_bid=avg_bid_rub,
                                traffic_volume=safe_int(row.get("AvgTrafficVolume")),
                                avg_position=safe_float(row.get("AvgImpressionPosition")),
                                avg_click_position=safe_float(row.get("AvgClickPosition")),
                                ctr=ctr_val,
                            ).on_conflict_do_update(
                                index_elements=["account_id", "keyword_id", "date"],
                                set_={
                                    "clicks": clicks,
                                    "spend": spend,
                                    "impressions": impressions,
                                    "ctr": ctr_val,
                                    "avg_cpc": avg_cpc_val,
                                    "avg_bid": avg_bid_rub,
                                    "avg_position": safe_float(row.get("AvgImpressionPosition")),
                                    "avg_click_position": safe_float(row.get("AvgClickPosition")),
                                    "traffic_volume": safe_int(row.get("AvgTrafficVolume")),
                                },
                            )
                            await db.execute(stmt)
                            saved_stats += 1
                        except (ValueError, KeyError) as e:
                            logger.warning(f"Error parsing stat row: {e} | row={row}")
                    await db.commit()
                    logger.info(f"Stats saved: {saved_stats} rows for account {account_id}")

            # ── Сбор поисковых запросов ──────────────────────────────────
            async with YandexDirectCollector(account.oauth_token, account.yandex_login) as dc2:
                try:
                    from app.models.models import SearchQuery
                    sq_data = await dc2.get_search_queries(date_from, date_to)
                    logger.info(f"Search queries for account {account_id}: {len(sq_data)} rows")
                    for row in sq_data:
                        try:
                            sq_date = datetime.strptime(row["Date"], "%Y-%m-%d")
                            sq_clicks = int(float(row.get("Clicks", 0) or 0))
                            sq_impressions = int(float(row.get("Impressions", 0) or 0))
                            if sq_clicks == 0 and sq_impressions == 0:
                                continue
                            kw_result = await db.execute(
                                select(Keyword).where(
                                    Keyword.account_id == account_id,
                                    Keyword.direct_id == str(row.get("CriterionId", "")),
                                )
                            )
                            kw = kw_result.scalar_one_or_none()
                            camp_result = await db.execute(
                                select(Campaign).where(
                                    Campaign.account_id == account_id,
                                    Campaign.direct_id == str(row.get("CampaignId", "")),
                                )
                            )
                            camp = camp_result.scalar_one_or_none()
                            ag_result = await db.execute(
                                select(AdGroup).where(
                                    AdGroup.account_id == account_id,
                                    AdGroup.direct_id == str(row.get("AdGroupId", "")),
                                )
                            )
                            ag = ag_result.scalar_one_or_none()

                            def safe_float(v):
                                try: return float(v) or None
                                except: return None

                            sq = SearchQuery(
                                account_id=account_id,
                                keyword_id=kw.id if kw else None,
                                date=sq_date,
                                query=row.get("Query", ""),
                                keyword_phrase=row.get("Criterion", ""),
                                match_type=row.get("MatchType", ""),
                                campaign_id=camp.id if camp else None,
                                ad_group_id=ag.id if ag else None,
                                impressions=sq_impressions,
                                clicks=sq_clicks,
                                spend=float(row.get("Cost", 0) or 0),
                                ctr=safe_float(row.get("Ctr")),
                                avg_cpc=safe_float(row.get("AvgCpc")),
                                avg_position=safe_float(row.get("AvgImpressionPosition")),
                                avg_click_position=safe_float(row.get("AvgClickPosition")),
                            )
                            db.add(sq)
                        except Exception as e:
                            logger.warning(f"Error saving search query: {e}")
                    await db.commit()
                    logger.info(f"Search queries saved for account {account_id}")
                except Exception as e:
                    logger.warning(f"Search queries collection failed: {e}")

            # ── Сбор из Метрики ──────────────────────────────────────────
            if account.metrika_counter_id:
                try:
                    from app.models.models import MetrikaSnapshot
                    async with MetrikaCollector(account.oauth_token, account.metrika_counter_id) as mc:
                        metrika_data = await mc.collect_all(date_from, date_to)
                        logger.info(
                            f"Metrika collected for account {account_id}: "
                            f"visits={metrika_data.get('summary', {}).get('visits', 0)}, "
                            f"days={len(metrika_data.get('by_day', []))}"
                        )
                        snapshot = MetrikaSnapshot(
                            account_id=account_id,
                            date=datetime.utcnow(),
                            data=metrika_data,
                        )
                        db.add(snapshot)
                        await db.commit()
                except Exception as e:
                    logger.warning(f"Metrika collection failed for account {account_id}: {e}")

            account.last_sync_at = datetime.utcnow()
            await db.commit()
            logger.info(f"Data collection complete for account {account_id}")

        run_analysis.delay(account_id)

    finally:
        await engine.dispose()


@celery_app.task(name="app.core.tasks.run_analysis", bind=True, max_retries=2)
def run_analysis(self, account_id: int):
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
                delta = (after_stats["clicks"] - before_stats["clicks"]) / before_stats["clicks"] * 100
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
