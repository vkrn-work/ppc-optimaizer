"""
Сelery задачи — сбор данных, анализ, трекинг гипотез.

Изменения v1.2:
  - Сохранение WeightedImpressions, WeightedCtr, BounceRate в keyword_stats
  - После сбора Метрики — обогащение sessions по utm_term в keyword_stats

CHANGED v1.3.0 (трекер гипотез):
  - track_all_hypotheses: track_until >= now → <= now (баг инверсии условия)
  - _track_hypothesis_async: поддержка ручных гипотез (suggestion_id=None) по object_id
  - порог значимости 10→5 кликов, динамика позиции в отчёте

CHANGED v1.6.0 (apply_suggestion):
  - add_negatives теперь работает и для object_type=ad_group (напрямую привязанных
    к группе предложений со страницы «Задачи ИИ»), а не только через
    object_type=keyword как раньше.
  - новый change_type=add_keywords — добавление новых ключевых слов в группу
    через keywords.add (источник: страница «Задачи ИИ», app.analyzers.agent_command).
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

            # ── Директ: кампании, группы, ключи ───
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
                            Keyword.direct_id == str(row.get("CriterionId", "")),
                        )
                    )
                    kw = kw_res.scalar_one_or_none()
                    if not kw:
                        continue
                    try:
                        stat_date  = datetime.strptime(row["Date"], "%Y-%m-%d")
                        clicks     = int(float(row.get("Clicks", 0) or 0))
                        impressions = int(float(row.get("Impressions", 0) or 0))
                        spend      = float(row.get("Cost", 0) or 0)
                        if clicks == 0 and impressions == 0:
                            continue

                        ctr_val = safe_float(row.get("Ctr"))
                        avg_bid_raw = safe_float(row.get("AvgEffectiveBid"))
                        avg_bid_rub = avg_bid_raw / 1_000_000 if avg_bid_raw else None
                        avg_cpc_val = safe_float(row.get("AvgCpc"))
                        w_ctr = safe_float(row.get("WeightedCtr"))
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
                            weighted_impressions=safe_int(row.get("WeightedImpressions")),
                            weighted_ctr=w_ctr,
                            bounce_rate=bounce_rate_val,
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

            # ── Метрика ────────────────
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

            # v1.7.0 (пункт 5 запроса): автономный ежедневный ИИ-анализ.
            # Раньше LLM-анализ запускался только вручную (кнопка на фронте
            # POST /run-llm-analysis) — CR-анализатор (rule-based) был
            # единственным, что реально работало по расписанию. Теперь после
            # каждого ежедневного сбора запускается ещё и LLM-анализ, если он
            # не отключён явно для этого аккаунта — директолог заходит утром
            # уже к готовым pending-предложениям, а не сам должен их запросить.
            cfg = account.analysis_config or {}
            autonomous_enabled = cfg.get("autonomous_llm_enabled", settings.AUTONOMOUS_LLM_ENABLED)
            llm_provider = cfg.get("llm_provider", settings.DEFAULT_LLM_PROVIDER)

        run_analysis.delay(account_id)
        if autonomous_enabled:
            run_llm_analysis.delay(account_id, period_days=28, provider=llm_provider)
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
                    # CHANGED: было track_until >= now (срок ещё НЕ наступил) —
                    # из-за чего ни одна гипотеза не доходила до оценки.
                    and_(Hypothesis.track_until <= now, Hypothesis.verdict == None)
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

            # CHANGED: поддержка и алгоритмических (через suggestion), и ручных
            # гипотез (suggestion_id=None, но object_id заполнен из UI).
            keyword_id = None
            object_type = hypothesis.object_type
            if hypothesis.suggestion_id:
                s_res = await db.execute(
                    select(Suggestion).where(Suggestion.id == hypothesis.suggestion_id)
                )
                suggestion = s_res.scalar_one_or_none()
                if suggestion:
                    object_type = suggestion.object_type
                    keyword_id = suggestion.object_id
            if keyword_id is None:
                keyword_id = hypothesis.object_id

            if object_type != "keyword" or not keyword_id:
                hypothesis.verdict = "insufficient"
                hypothesis.report = "Трекинг доступен только для ключевых гипотез."
                await db.commit()
                return

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

            # CHANGED: порог смягчён 10→5 (нишевой B2B даёт мало кликов),
            # в отчёт добавлена динамика позиции как вторичный сигнал.
            def pos_note(b, a):
                if b.get("avg_position") and a.get("avg_position"):
                    d = b["avg_position"] - a["avg_position"]
                    if abs(d) >= 0.3:
                        return f" Позиция {'улучшилась' if d > 0 else 'ухудшилась'} ({b['avg_position']}→{a['avg_position']})."
                return ""

            if before["clicks"] < 5 or after["clicks"] < 5:
                hypothesis.verdict = "insufficient"
                hypothesis.report  = (
                    f"Недостаточно кликов для значимого вывода "
                    f"(до {before['clicks']}, после {after['clicks']})."
                )
            else:
                delta = (after["clicks"] - before["clicks"]) / before["clicks"] * 100
                hypothesis.delta_percent = round(delta, 2)
                note = pos_note(before, after)
                if delta >= 10:
                    hypothesis.verdict = "confirmed"
                    hypothesis.report  = f"Гипотеза подтверждена. Трафик вырос на {delta:.1f}%.{note}"
                elif delta <= -10:
                    hypothesis.verdict = "rejected"
                    hypothesis.report  = f"Гипотеза отклонена. Трафик упал на {abs(delta):.1f}%.{note}"
                else:
                    hypothesis.verdict = "neutral"
                    hypothesis.report  = f"Изменение нейтральное ({delta:+.1f}%). Продолжить наблюдение.{note}"

            await db.commit()
            logger.info(f"Hypothesis {hypothesis_id} → {hypothesis.verdict}")
    finally:
        await engine.dispose()


# ─── LLM-анализ ──────────────────────────────────

@celery_app.task(name="app.core.tasks.run_llm_analysis", bind=True, max_retries=1)
def run_llm_analysis(self, account_id: int, period_days: int = 28, provider: str = "claude"):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_run_llm_analysis_async(account_id, period_days, provider))
    finally:
        loop.close()
        asyncio.set_event_loop(None)


async def _run_llm_analysis_async(account_id: int, period_days: int = 28, provider: str = "claude"):
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from app.core.config import settings
    from app.analyzers.llm_analyzer import LLMAnalyzer

    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with AsyncSessionLocal() as db:
            analyzer = LLMAnalyzer(db, account_id)
            suggestions = await analyzer.generate_suggestions(period_days, provider=provider)
            logger.info(f"LLM analysis done for account {account_id} (provider={provider}): {len(suggestions)} suggestions")
            return len(suggestions)
    finally:
        await engine.dispose()


# ─── Применение изменений в Яндекс.Директ ──────────────
#
# Вызывается ПОСЛЕ того, как suggestion.status уже переведён в approved
# через POST /suggestions/{id}/action. Здесь происходит фактическая запись
# в кабинет через Direct API v5 (write). При успехе status -> applied,
# при ошибке -> approved остаётся (можно повторить) + причина в reject_reason.

@celery_app.task(name="app.core.tasks.apply_suggestion", bind=True, max_retries=1)
def apply_suggestion(self, suggestion_id: int):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_apply_suggestion_async(suggestion_id))
    finally:
        loop.close()
        asyncio.set_event_loop(None)


async def _apply_suggestion_async(suggestion_id: int) -> dict:
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from sqlalchemy import select
    from app.core.config import settings
    from app.models.models import Suggestion, SuggestionStatus, Keyword, AdGroup, Account
    from app.collectors.direct_writer import YandexDirectWriter

    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(Suggestion).where(Suggestion.id == suggestion_id))
            s = res.scalar_one_or_none()
            if not s:
                return {"status": "error", "detail": "suggestion not found"}
            if s.status != SuggestionStatus.approved:
                return {"status": "error", "detail": f"suggestion status is {s.status}, expected approved"}

            acc_res = await db.execute(select(Account).where(Account.id == s.account_id))
            account = acc_res.scalar_one_or_none()
            if not account or not account.oauth_token:
                return {"status": "error", "detail": "account has no oauth_token"}

            kw = None
            if s.object_type == "keyword" and s.object_id:
                kw_res = await db.execute(select(Keyword).where(Keyword.id == s.object_id))
                kw = kw_res.scalar_one_or_none()

            # v1.6.0: предложения со страницы «Задачи ИИ» привязаны напрямую к
            # группе объявлений (object_type=ad_group), а не к ключу.
            ag_direct = None
            if s.object_type == "ad_group" and s.object_id:
                ag_res = await db.execute(select(AdGroup).where(AdGroup.id == s.object_id))
                ag_direct = ag_res.scalar_one_or_none()

            ok, detail = False, "unknown change_type"
            async with YandexDirectWriter(account.oauth_token, account.yandex_login) as writer:
                if s.change_type in ("bid_raise", "bid_lower"):
                    if not kw:
                        ok, detail = False, "keyword not found in DB"
                    else:
                        try:
                            new_bid = float(str(s.value_after).replace("₽", "").strip())
                        except (ValueError, TypeError):
                            ok, detail = False, f"cannot parse value_after={s.value_after}"
                        else:
                            ok, detail = await writer.set_keyword_bid(kw.direct_id, new_bid)

                elif s.change_type == "add_negatives":
                    # v1.6.0: минус-слова могут прийти либо от обычного LLM-анализа
                    # (object_type=keyword — группа определяется через ключ), либо от
                    # страницы «Задачи ИИ» (object_type=ad_group — группа уже указана
                    # напрямую).
                    ag = ag_direct
                    if not ag and kw:
                        ag_res = await db.execute(select(AdGroup).where(AdGroup.id == kw.ad_group_id))
                        ag = ag_res.scalar_one_or_none()
                    negatives = [w.strip() for w in (s.value_after or "").split(",") if w.strip()]
                    # Защита: старый rule-based генератор (cr_analyzer 8A) кладёт в
                    # value_after текстовую инструкцию ("Добавить минус-слова по мусорным
                    # запросам"), а не реальный список слов — такие значения нельзя
                    # отправлять в Direct как NegativeKeywords. Отсекаем по эвристике:
                    # настоящее минус-слово короткое и не похоже на предложение.
                    looks_like_sentence = any(
                        len(w) > 30 or "." in w or w.count(" ") > 4 for w in negatives
                    )
                    if not ag or not negatives:
                        ok, detail = False, "no ad_group or empty negatives list"
                    elif looks_like_sentence:
                        ok, detail = False, (
                            "value_after похож на текстовое описание, а не список минус-слов "
                            "(вероятно, предложение создано старым rule-based анализатором без "
                            "реального списка слов) — применение заблокировано, нужна ручная проверка"
                        )
                    else:
                        ok, detail = await writer.add_negative_keywords(ag.direct_id, negatives)

                elif s.change_type == "add_keywords":
                    # v1.6.0: новые ключевые слова со страницы «Задачи ИИ» — привязаны
                    # напрямую к группе объявлений (object_type=ad_group).
                    keywords = [w.strip() for w in (s.value_after or "").split(",") if w.strip()]
                    if not ag_direct or not keywords:
                        ok, detail = False, "no ad_group or empty keywords list"
                    else:
                        ok, detail = await writer.add_keywords(ag_direct.direct_id, keywords)

                elif s.change_type in ("pause", "site_check"):
                    if not kw:
                        ok, detail = False, "keyword not found in DB"
                    else:
                        ok, detail = await writer.suspend_keyword(kw.direct_id)

                elif s.change_type == "create_campaign":
                    # v1.7.0 (пункт 6): создание кампании целиком из черновика
                    # (см. app/generators/campaign_planner.py). Единственный
                    # путь записи в Директ, ни разу не проверенный на живом
                    # аккаунте — см. предупреждение в direct_writer.py
                    # create_campaign(). Best-effort: даже при частичном
                    # провале (например, одна группа не создалась) кампания и
                    # успешные группы остаются в кабинете, отчёт по каждому
                    # шагу сохраняется в payload для разбора вручную.
                    if not s.payload or not s.payload.get("ad_groups"):
                        ok, detail = False, "suggestion.payload пустой или без групп — нечего создавать"
                    else:
                        report = await writer.create_full_campaign(s.payload)
                        ok = bool(report.get("campaign_ok"))
                        groups_ok = sum(1 for g in report.get("groups", []) if g.get("ok"))
                        groups_total = len(report.get("groups", []))
                        detail = (
                            f"campaign_id={report.get('campaign_id')}, групп создано {groups_ok}/{groups_total}"
                            if ok else report.get("detail", "campaign creation failed")
                        )
                        s.payload = {**s.payload, "apply_report": report}

                else:
                    ok, detail = False, f"change_type '{s.change_type}' requires manual action (not auto-applicable)"

            if ok:
                s.status = SuggestionStatus.applied
                s.applied_at = datetime.utcnow()
            else:
                s.reject_reason = f"Apply failed: {detail}"
            await db.commit()
            logger.info(f"apply_suggestion {suggestion_id}: ok={ok} detail={detail}")
            return {"status": "applied" if ok else "failed", "detail": detail}
    finally:
        await engine.dispose()
