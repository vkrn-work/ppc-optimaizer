"""
Celery задачи — сбор данных, анализ, трекинг гипотез.

Изменения v1.2:
  - Сохранение WeightedImpressions, WeightedCtr, BounceRate в keyword_stats
  - После сбора Метрики — обогащение sessions по utm_term в keyword_stats
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
    engine  = create_engine(sync_url, pool_pre_ping=True)
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
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_collect_account_data_async(account_id, days=days))
    finally:
        loop.close()
        asyncio.set_event_loop(None)


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
            acc_result = await db.execute(
                select(Account).where(Account.id == account_id)
            )
            account = acc_result.scalar_one_or_none()
            if not account or not account.oauth_token:
                logger.error(f"Account {account_id} not found or no token")
                return

            date_to   = date.today()
            date_from = date_to - timedelta(days=days)
            logger.info(
                f"Collecting account {account_id} for {days} days:"
                f" {date_from} — {date_to}"
            )

            # ── Директ: кампании, группы, ключи ─────────────────────────
            async with YandexDirectCollector(
                account.oauth_token, account.yandex_login
            ) as dc:
                campaigns_data = await dc.get_campaigns()
                logger.info(f"Campaigns: {len(campaigns_data)}")

                for c in campaigns_data:
                    strategy = c.get("_strategy", "UNKNOWN")
                    stmt = insert(Campaign).values(
                        account_id=account_id,
                        direct_id=str(c["Id"]),
                        name=c.get("Name", ""),
                        campaign_type=c.get("Type", ""),
                        status=c.get("Status", ""),
                        strategy_type=strategy,
                        is_active=True,
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
                if not campaign_ids:
                    return

                # Группы
                groups_data = []
                for i in range(0, len(campaign_ids), 10):
                    batch = campaign_ids[i:i+10]
                    groups_data.extend(await dc.get_ad_groups(batch))
                logger.info(f"AdGroups: {len(groups_data)}")
                for g in groups_data:
                    camp_res = await db.execute(
                        select(Campaign).where(
                            Campaign.account_id == account_id,
                            Campaign.direct_id == str(g["CampaignId"]),
                        )
                    )
                    camp = camp_res.scalar_one_or_none()
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
                    keywords_data.extend(await dc.get_keywords(batch))
                logger.info(f"Keywords: {len(keywords_data)}")
                for kw in keywords_data:
                    group_res = await db.execute(
                        select(AdGroup).where(
                            AdGroup.account_id == account_id,
                            AdGroup.direct_id == str(kw["AdGroupId"]),
                        )
                    )
                    group = group_res.scalar_one_or_none()
                    if not group:
                        continue
                    bid = kw.get("Bid")
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

                # Статистика ключей — с новыми полями
                stats_data = await dc.get_keyword_stats(date_from, date_to)
                logger.info(f"Keyword stats rows: {len(stats_data)}")
                saved_stats = 0

                def safe_float(v):
                    try:
                        r = float(v)
                        return r if r > 0 else None
                    except Exception:
                        return None

                def safe_int(v):
                    try:
                        r = int(float(v))
                        return r if r > 0 else None
                    except Exception:
                        return None

                for row in stats_data:
                    kw_res = await db.execute(
            select(Keyword).where(
                Keyword.account_id == account_id,
                Keyword.phrase == utm_term,
            ).limit(1)
        )
        kw = kw_res.scalars().first()
        if not kw:
            continue
                    try:
                        stat_date  = datetime.strptime(row["Date"], "%Y-%m-%d")
                        clicks     = int(float(row.get("Clicks", 0) or 0))
                        impressions = int(float(row.get("Impressions", 0) or 0))
                        spend      = float(row.get("Cost", 0) or 0)
                        if clicks == 0 and impressions == 0:
                            continue

                        # CTR в процентах ("5.23")
                        ctr_val = safe_float(row.get("Ctr"))

                        # AvgEffectiveBid в микрорублях → рубли
                        avg_bid_raw = safe_float(row.get("AvgEffectiveBid"))
                        avg_bid_rub = avg_bid_raw / 1_000_000 if avg_bid_raw else None

                        # AvgCpc уже в рублях
                        avg_cpc_val = safe_float(row.get("AvgCpc"))

                        # WeightedCtr в процентах
                        w_ctr = safe_float(row.get("WeightedCtr"))

                        # BounceRate в процентах (--  если нет данных API вернёт "--")
                        br_raw = row.get("BounceRate", "")
                        bounce_rate_val = safe_float(br_raw) if br_raw != "--" else None

                        stmt = insert(KeywordStat).values(
                            account_id=account_id,
                            keyword_id=kw.id,
                            date=stat_date,
                            impressions=impressions,
                            clicks=clicks,
                            spend=spend,
                            ctr=ctr_val,
                            avg_cpc=avg_cpc_val,
                            avg_bid=avg_bid_rub,
                            avg_position=safe_float(row.get("AvgImpressionPosition")),
                            avg_click_position=safe_float(row.get("AvgClickPosition")),
                            traffic_volume=safe_int(row.get("AvgTrafficVolume")),
                            # ── Новые поля v1.2 ────────────────────────
                            weighted_impressions=safe_int(row.get("WeightedImpressions")),
                            weighted_ctr=w_ctr,
                            bounce_rate=bounce_rate_val,
                            # sessions будет заполнен при обогащении из Метрики
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
                                "weighted_impressions": safe_int(row.get("WeightedImpressions")),
                                "weighted_ctr": w_ctr,
                                "bounce_rate": bounce_rate_val,
                            },
                        )
                        await db.execute(stmt)
                        saved_stats += 1
                    except (ValueError, KeyError) as e:
                        logger.warning(f"Error parsing stat row: {e} | row={row}")
                await db.commit()
                logger.info(f"Stats saved: {saved_stats} rows for account {account_id}")

                # Поисковые запросы
                try:
                    from app.models.models import SearchQuery
                    sq_data = await dc.get_search_queries(date_from, date_to)
                    logger.info(f"Search queries: {len(sq_data)}")
                    for row in sq_data:
                        try:
                            sq_date = datetime.strptime(row["Date"], "%Y-%m-%d")
                            sq_clicks = int(float(row.get("Clicks", 0) or 0))
                            sq_impr   = int(float(row.get("Impressions", 0) or 0))
                            if sq_clicks == 0 and sq_impr == 0:
                                continue

                            kw_res = await db.execute(
                                select(Keyword).where(
                                    Keyword.account_id == account_id,
                                    Keyword.direct_id == str(row.get("CriterionId", "")),
                                )
                            )
                            kw  = kw_res.scalar_one_or_none()
                            cp_res = await db.execute(
                                select(Campaign).where(
                                    Campaign.account_id == account_id,
                                    Campaign.direct_id == str(row.get("CampaignId", "")),
                                )
                            )
                            cp  = cp_res.scalar_one_or_none()
                            ag_res = await db.execute(
                                select(AdGroup).where(
                                    AdGroup.account_id == account_id,
                                    AdGroup.direct_id == str(row.get("AdGroupId", "")),
                                )
                            )
                            ag = ag_res.scalar_one_or_none()

                            db.add(SearchQuery(
                                account_id=account_id,
                                keyword_id=kw.id if kw else None,
                                date=sq_date,
                                query=row.get("Query", ""),
                                keyword_phrase=row.get("Criterion", ""),
                                match_type=row.get("MatchType", ""),
                                campaign_id=cp.id if cp else None,
                                ad_group_id=ag.id if ag else None,
                                impressions=sq_impr,
                                clicks=sq_clicks,
                                spend=float(row.get("Cost", 0) or 0),
                                ctr=safe_float(row.get("Ctr")),
                                avg_cpc=safe_float(row.get("AvgCpc")),
                                avg_position=safe_float(row.get("AvgImpressionPosition")),
                                avg_click_position=safe_float(row.get("AvgClickPosition")),
                            ))
                        except Exception as e:
                            logger.warning(f"Error saving search query: {e}")
                    await db.commit()
                except Exception as e:
                    logger.warning(f"Search queries collection failed: {e}")

            # ── Метрика ──────────────────────────────────────────────────
            if account.metrika_counter_id:
                try:
                    from app.models.models import MetrikaSnapshot
                    async with MetrikaCollector(
                        account.oauth_token, account.metrika_counter_id
                    ) as mc:
                        metrika_data = await mc.collect_all(date_from, date_to)
                        logger.info(
                            f"Metrika collected: visits="
                            f"{metrika_data.get('summary', {}).get('visits', 0)}"
                        )
                        snap = MetrikaSnapshot(
                            account_id=account_id,
                            date=datetime.utcnow(),
                            data=metrika_data,
                        )
                        db.add(snap)
                        await db.commit()

                        # ── Обогащение sessions в keyword_stats ───────────
                        # Матчим по utm_term → keyword.phrase → keyword_stats
                        kw_data = metrika_data.get("keywords", [])
                        if kw_data:
                            await _enrich_sessions(
                                db, account_id, kw_data, date_from, date_to
                            )
                except Exception as e:
                    logger.warning(f"Metrika collection failed: {e}")

            account.last_sync_at = datetime.utcnow()
            await db.commit()
            logger.info(f"Data collection complete for account {account_id}")

        run_analysis.delay(account_id)
    finally:
        await engine.dispose()


async def _enrich_sessions(db, account_id, kw_metrika: list, date_from, date_to):
    """
    Обогащает keyword_stats полем sessions из данных Метрики.
    Матчинг: utm_term (Метрика) == keyword.phrase (Директ).
    Это приближение — точный матчинг требует client_id/roistat_id.
    """
    from sqlalchemy import select, update, and_
    from app.models.models import Keyword, KeywordStat

    enriched = 0
    for row in kw_metrika:
        utm_term = row.get("UTMTerm") or row.get("UTMMedium") or ""
        visits   = int(row.get("visits", 0) or 0)
        if not utm_term or visits == 0:
            continue

        kw_res = await db.execute(
            select(Keyword).where(
                Keyword.account_id == account_id,
                Keyword.phrase == utm_term,
            ).limit(1)
        )
        kw = kw_res.scalars().first()
        if not kw:
            continue

        # Обновляем все строки за период (приближённо распределяем visits)
        await db.execute(
            update(KeywordStat)
            .where(and_(
                KeywordStat.account_id == account_id,
                KeywordStat.keyword_id == kw.id,
                KeywordStat.date >= datetime.combine(date_from, datetime.min.time()),
                KeywordStat.date <= datetime.combine(date_to, datetime.min.time()),
                KeywordStat.sessions == None,
            ))
            .values(sessions=visits)
        )
        enriched += 1

    await db.commit()
    if enriched:
        logger.info(f"Sessions enriched for {enriched} keywords, account {account_id}")


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
            analyzer    = CRAnalyzer(db, account_id)
            analysis    = await analyzer.run_full_analysis()
            generator   = SuggestionGenerator(db, account_id)
            suggestions = await generator.generate_for_analysis(analysis)
            scale_s     = await generator.generate_scale_suggestions(analysis)
            logger.info(
                f"Analysis done for account {account_id}:"
                f" {len(suggestions) + len(scale_s)} suggestions"
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
            now    = datetime.utcnow()
            result = await db.execute(
                select(Hypothesis).where(
                    and_(Hypothesis.track_until >= now, Hypothesis.verdict == None)
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
            h_res = await db.execute(
                select(Hypothesis).where(Hypothesis.id == hypothesis_id)
            )
            hypothesis = h_res.scalar_one_or_none()
            if not hypothesis:
                return

            now = datetime.utcnow()
            if now < hypothesis.track_until:
                return

            s_res = await db.execute(
                select(Suggestion).where(Suggestion.id == hypothesis.suggestion_id)
            )
            suggestion = s_res.scalar_one_or_none()
            if not suggestion or suggestion.object_type != "keyword":
                return

            keyword_id = suggestion.object_id
            applied_at = hypothesis.applied_at

            async def get_stats(start, end):
                res = await db.execute(
                    select(
                        func.sum(KeywordStat.clicks).label("clicks"),
                        func.sum(KeywordStat.spend).label("spend"),
                        func.avg(KeywordStat.avg_position).label("avg_position"),
                        func.avg(KeywordStat.ctr).label("ctr"),
                    ).where(and_(
                        KeywordStat.keyword_id == keyword_id,
                        KeywordStat.date >= start,
                        KeywordStat.date <= end,
                    ))
                )
                row = res.one()
                return {
                    "clicks":       int(row.clicks or 0),
                    "spend":        float(row.spend or 0),
                    "avg_position": round(float(row.avg_position), 2) if row.avg_position else None,
                    "ctr":          round(float(row.ctr), 2) if row.ctr else None,
                }

            before = await get_stats(applied_at - timedelta(days=7), applied_at)
            after  = await get_stats(applied_at, applied_at + timedelta(days=7))

            hypothesis.metrics_before = before
            hypothesis.metrics_after  = after

            if before["clicks"] < 10 or after["clicks"] < 10:
                hypothesis.verdict = "insufficient"
                hypothesis.report  = "Недостаточно кликов для статистически значимого вывода."
            else:
                delta = (after["clicks"] - before["clicks"]) / before["clicks"] * 100
                hypothesis.delta_percent = round(delta, 2)
                if delta >= 10:
                    hypothesis.verdict = "confirmed"
                    hypothesis.report  = f"Гипотеза подтверждена. Трафик вырос на {delta:.1f}%."
                elif delta <= -10:
                    hypothesis.verdict = "rejected"
                    hypothesis.report  = f"Гипотеза отклонена. Трафик упал на {abs(delta):.1f}%."
                else:
                    hypothesis.verdict = "neutral"
                    hypothesis.report  = f"Изменение нейтральное ({delta:+.1f}%). Продолжить наблюдение."

            await db.commit()
            logger.info(f"Hypothesis {hypothesis_id} → {hypothesis.verdict}")
    finally:
        await engine.dispose()
