"""
CR-анализатор — ядро аналитического движка.
Реализует двухуровневую модель ставок из воркфлоу (раздел 8.2).
"""
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional
import logging
from sqlalchemy import select, func, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    Keyword, KeywordStat, Lead, Cluster,
    KeywordMetrics, AnalysisResult, Campaign, AdGroup
)
from app.core.config import settings

logger = logging.getLogger(__name__)


class CRAnalyzer:
    def __init__(self, db: AsyncSession, account_id: int):
        self.db = db
        self.account_id = account_id

    async def run_full_analysis(self, period_days: int = 28) -> AnalysisResult:
        """Запустить полный CR-анализ по всем ключам и кластерам"""
        period_end = datetime.utcnow()
        period_start = period_end - timedelta(days=period_days)

        # Создать запись результата анализа
        analysis = AnalysisResult(
            account_id=self.account_id,
            period_start=period_start,
            period_end=period_end,
        )
        self.db.add(analysis)
        await self.db.flush()

        # Рассчитать метрики по всем ключам
        keyword_metrics = await self._calculate_keyword_metrics(analysis.id, period_start, period_end)

        # Рассчитать метрики по кластерам
        cluster_metrics = await self._calculate_cluster_metrics(period_start, period_end)

        # Применить двухуровневую модель ставок
        for km in keyword_metrics:
            km = await self._apply_bid_model(km, cluster_metrics)

        # Собрать сводку
        analysis.summary = await self._build_summary(keyword_metrics, cluster_metrics, period_start, period_end)
        analysis.problems = await self._detect_problems(keyword_metrics, cluster_metrics)
        analysis.opportunities = await self._detect_opportunities(keyword_metrics, cluster_metrics)

        await self.db.commit()
        logger.info(f"Analysis {analysis.id} complete for account {self.account_id}")
        return analysis

    async def _calculate_keyword_metrics(
        self,
        analysis_id: int,
        period_start: datetime,
        period_end: datetime,
    ) -> list[KeywordMetrics]:
        """Рассчитать CR, CPL, CPQL по каждому ключу за период"""
        # Агрегируем статистику кликов из Директа
        clicks_query = (
            select(
                KeywordStat.keyword_id,
                func.sum(KeywordStat.clicks).label("total_clicks"),
                func.sum(KeywordStat.spend).label("total_spend"),
            )
            .where(
                and_(
                    KeywordStat.account_id == self.account_id,
                    KeywordStat.date >= period_start,
                    KeywordStat.date <= period_end,
                )
            )
            .group_by(KeywordStat.keyword_id)
        )
        clicks_result = await self.db.execute(clicks_query)
        clicks_by_keyword = {row.keyword_id: row for row in clicks_result}

        # Агрегируем лиды из CRM по utm_term
        leads_query = text("""
            SELECT k.id as keyword_id,
                   COUNT(l.id) as total_leads,
                   COUNT(CASE WHEN l.is_qualified THEN 1 END) as total_sqls
            FROM keywords k
            LEFT JOIN leads l ON l.utm_term ILIKE k.phrase
                AND l.account_id = :account_id
                AND l.created_at BETWEEN :period_start AND :period_end
            WHERE k.account_id = :account_id
            GROUP BY k.id
        """)
        leads_result = await self.db.execute(leads_query, {
            "account_id": self.account_id,
            "period_start": period_start,
            "period_end": period_end,
        })
        leads_by_keyword = {row.keyword_id: row for row in leads_result}

        metrics_list = []
        for kw_id, click_row in clicks_by_keyword.items():
            clicks = int(click_row.total_clicks or 0)
            spend = Decimal(str(click_row.total_spend or 0))
            lead_row = leads_by_keyword.get(kw_id)
            leads = int(lead_row.total_leads or 0) if lead_row else 0
            sqls = int(lead_row.total_sqls or 0) if lead_row else 0

            cr_click_lead = Decimal(str(leads / clicks)) if clicks > 0 else None
            cr_lead_sql = Decimal(str(sqls / leads)) if leads > 0 else None
            cpl = (spend / leads) if leads > 0 else None
            cpql = (spend / sqls) if sqls > 0 else None
            is_significant = clicks >= settings.MIN_CLICKS_KEYWORD

            km = KeywordMetrics(
                account_id=self.account_id,
                keyword_id=kw_id,
                analysis_id=analysis_id,
                period_days=28,
                clicks=clicks,
                spend=spend,
                leads=leads,
                sqls=sqls,
                cr_click_lead=cr_click_lead,
                cr_lead_sql=cr_lead_sql,
                cpl=cpl,
                cpql=cpql,
                is_significant=is_significant,
                bid_source="individual" if is_significant else "cluster",
            )
            self.db.add(km)
            metrics_list.append(km)

        await self.db.flush()
        return metrics_list

    async def _calculate_cluster_metrics(
        self,
        period_start: datetime,
        period_end: datetime,
    ) -> dict[int, dict]:
        """Рассчитать агрегированные CR по каждому кластеру"""
        query = text("""
            SELECT
                k.cluster_id,
                SUM(ks.clicks) as total_clicks,
                SUM(ks.spend) as total_spend,
                COUNT(DISTINCT l.id) as total_leads,
                COUNT(DISTINCT CASE WHEN l.is_qualified THEN l.id END) as total_sqls
            FROM keywords k
            JOIN keyword_stats ks ON ks.keyword_id = k.id
                AND ks.date BETWEEN :period_start AND :period_end
            LEFT JOIN leads l ON l.utm_term ILIKE k.phrase
                AND l.account_id = :account_id
                AND l.created_at BETWEEN :period_start AND :period_end
            WHERE k.account_id = :account_id
                AND k.cluster_id IS NOT NULL
            GROUP BY k.cluster_id
        """)
        result = await self.db.execute(query, {
            "account_id": self.account_id,
            "period_start": period_start,
            "period_end": period_end,
        })
        cluster_data = {}
        for row in result:
            clicks = int(row.total_clicks or 0)
            leads = int(row.total_leads or 0)
            sqls = int(row.total_sqls or 0)
            spend = Decimal(str(row.total_spend or 0))
            cluster_data[row.cluster_id] = {
                "clicks": clicks,
                "leads": leads,
                "sqls": sqls,
                "spend": spend,
                "cr_click_lead": Decimal(str(leads / clicks)) if clicks > 0 else Decimal("0"),
                "cr_lead_sql": Decimal(str(sqls / leads)) if leads > 0 else Decimal("0"),
                "cpl": spend / leads if leads > 0 else None,
                "cpql": spend / sqls if sqls > 0 else None,
            }
        return cluster_data

    async def _apply_bid_model(
        self,
        km: KeywordMetrics,
        cluster_metrics: dict[int, dict],
    ) -> KeywordMetrics:
        """
        Двухуровневая модель ставок (воркфлоу раздел 8.2):
        Уровень 1: ставка_кластера = целевой_CPL × CR_кластера
        Уровень 2: ставка_ключа = ставка_кластера × (CR_ключа / CR_кластера)
        """
        # Получить ключ и его кластер
        kw_result = await self.db.execute(
            select(Keyword).where(Keyword.id == km.keyword_id)
        )
        keyword = kw_result.scalar_one_or_none()
        if not keyword:
            return km

        # Получить целевой CPL (из кластера, потом из аккаунта)
        target_cpl = None
        if keyword.cluster_id and keyword.cluster_id in cluster_metrics:
            cluster_result = await self.db.execute(
                select(Cluster).where(Cluster.id == keyword.cluster_id)
            )
            cluster = cluster_result.scalar_one_or_none()
            if cluster and cluster.target_cpl:
                target_cpl = cluster.target_cpl

        if not target_cpl:
            # Fallback на целевой CPL аккаунта
            from app.models.models import Account
            acc_result = await self.db.execute(
                select(Account).where(Account.id == self.account_id)
            )
            acc = acc_result.scalar_one_or_none()
            if acc and acc.target_cpl:
                target_cpl = acc.target_cpl

        if not target_cpl:
            return km

        # Уровень 1: базовая ставка кластера
        if keyword.cluster_id and keyword.cluster_id in cluster_metrics:
            cluster_cr = cluster_metrics[keyword.cluster_id]["cr_click_lead"]
            if cluster_cr > 0:
                bid_cluster = target_cpl * cluster_cr
            else:
                bid_cluster = None
        else:
            bid_cluster = None

        # Уровень 2: индивидуальная ставка ключа (только если значимо)
        if km.is_significant and km.cr_click_lead and bid_cluster:
            cluster_cr = cluster_metrics.get(keyword.cluster_id, {}).get("cr_click_lead", Decimal("0"))
            if cluster_cr > 0:
                coeff = km.cr_click_lead / cluster_cr
                km.recommended_bid = bid_cluster * coeff
                km.bid_source = "individual"
            else:
                km.recommended_bid = bid_cluster
                km.bid_source = "cluster"
        elif bid_cluster:
            km.recommended_bid = bid_cluster
            km.bid_source = "cluster"

        return km

    async def _build_summary(
        self,
        keyword_metrics: list[KeywordMetrics],
        cluster_metrics: dict,
        period_start: datetime,
        period_end: datetime,
    ) -> dict:
        """Собрать сводные KPI кабинета"""
        total_clicks = sum(km.clicks for km in keyword_metrics)
        total_spend = sum(km.spend for km in keyword_metrics)
        total_leads = sum(km.leads for km in keyword_metrics)
        total_sqls = sum(km.sqls for km in keyword_metrics)

        return {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "total_clicks": total_clicks,
            "total_spend": float(total_spend),
            "total_leads": total_leads,
            "total_sqls": total_sqls,
            "cr_click_lead": round(total_leads / total_clicks * 100, 2) if total_clicks > 0 else 0,
            "cr_lead_sql": round(total_sqls / total_leads * 100, 2) if total_leads > 0 else 0,
            "cpl": round(float(total_spend) / total_leads, 2) if total_leads > 0 else None,
            "cpql": round(float(total_spend) / total_sqls, 2) if total_sqls > 0 else None,
            "avg_cpc": round(float(total_spend) / total_clicks, 2) if total_clicks > 0 else None,
            "keywords_analyzed": len(keyword_metrics),
            "keywords_significant": sum(1 for km in keyword_metrics if km.is_significant),
        }

    async def _detect_problems(
        self,
        keyword_metrics: list[KeywordMetrics],
        cluster_metrics: dict,
    ) -> list[dict]:
        """Автоматически выявить топ-5 проблем (логика из плейбука раздел 4.3)"""
        problems = []

        for km in keyword_metrics:
            if not km.is_significant:
                continue
            cr = float(km.cr_click_lead or 0)
            kw_result = await self.db.execute(
                select(Keyword).where(Keyword.id == km.keyword_id)
            )
            kw = kw_result.scalar_one_or_none()
            if not kw:
                continue

            # Нулевой CR при большом числе кликов — информационный трафик
            if cr < settings.CR_CRITICAL_THRESHOLD and km.clicks >= 100:
                problems.append({
                    "type": "zero_cr",
                    "severity": "critical",
                    "keyword_id": km.keyword_id,
                    "phrase": kw.phrase,
                    "clicks": km.clicks,
                    "cr": round(cr * 100, 2),
                    "spend": float(km.spend),
                    "description": f"CR < 1% при {km.clicks} кликах. Вероятен информационный интент.",
                    "action": "Проверить поисковые фразы. Добавить минус-слова или отключить.",
                })

            # CPL сильно выше цели
            if km.cpl:
                from app.models.models import Account
                acc_result = await self.db.execute(
                    select(Account).where(Account.id == self.account_id)
                )
                acc = acc_result.scalar_one_or_none()
                if acc and acc.target_cpl and km.cpl > acc.target_cpl * Decimal("1.5"):
                    problems.append({
                        "type": "high_cpl",
                        "severity": "warning",
                        "keyword_id": km.keyword_id,
                        "phrase": kw.phrase,
                        "cpl": float(km.cpl),
                        "target_cpl": float(acc.target_cpl),
                        "description": f"CPL {float(km.cpl):.0f}₽ превышает цель в 1.5×.",
                        "action": "Снизить ставку на 20% или улучшить посадочную страницу.",
                    })

        # Сортировать по расходу (проблемы с наибольшим влиянием первыми)
        problems.sort(key=lambda x: x.get("spend", x.get("cpl", 0)), reverse=True)
        return problems[:5]

    async def _detect_opportunities(
        self,
        keyword_metrics: list[KeywordMetrics],
        cluster_metrics: dict,
    ) -> list[dict]:
        """Выявить точки роста — ключи с высоким CR для масштабирования"""
        opportunities = []
        for km in keyword_metrics:
            if not km.is_significant:
                continue
            cr = float(km.cr_click_lead or 0)
            if cr > settings.CR_HIGH_THRESHOLD:
                kw_result = await self.db.execute(
                    select(Keyword).where(Keyword.id == km.keyword_id)
                )
                kw = kw_result.scalar_one_or_none()
                if kw:
                    opportunities.append({
                        "type": "high_cr_keyword",
                        "keyword_id": km.keyword_id,
                        "phrase": kw.phrase,
                        "cr": round(cr * 100, 2),
                        "clicks": km.clicks,
                        "leads": km.leads,
                        "cpl": float(km.cpl) if km.cpl else None,
                        "recommended_bid": float(km.recommended_bid) if km.recommended_bid else None,
                        "description": f"CR {round(cr*100, 1)}% — конверсионный ключ. Увеличить ставку для роста трафика.",
                        "action": f"Поднять ставку до {float(km.recommended_bid):.0f}₽" if km.recommended_bid else "Поднять ставку",
                    })
        opportunities.sort(key=lambda x: x["cr"], reverse=True)
        return opportunities[:5]
