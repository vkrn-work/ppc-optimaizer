"""
CR-анализатор — уровень 1 (только рекламные данные без CRM).

Логика анализа:
- Падение объёма трафика при стабильном рынке → ставок не хватает
- Позиция показа > 3 → ставка низкая
- CTR ниже нормы для позиции → проблема с объявлением
- Высокий bounce rate из Метрики → нерелевантный трафик
- Нулевой CTR при показах → объявление не работает
"""
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional
import logging
from sqlalchemy import select, func, and_, text, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    Keyword, KeywordStat, Cluster, Campaign,
    KeywordMetrics, AnalysisResult, AdGroup, SearchQuery
)
from app.core.config import settings

logger = logging.getLogger(__name__)


class CRAnalyzer:
    def __init__(self, db: AsyncSession, account_id: int):
        self.db = db
        self.account_id = account_id

    async def run_full_analysis(self, period_days: int = 28) -> AnalysisResult:
        """Запустить полный анализ уровня 1"""
        period_end = datetime.utcnow()
        period_start = period_end - timedelta(days=period_days)

        # Предыдущий период для сравнения
        prev_end = period_start
        prev_start = prev_end - timedelta(days=period_days)

        analysis = AnalysisResult(
            account_id=self.account_id,
            period_start=period_start,
            period_end=period_end,
        )
        self.db.add(analysis)
        await self.db.flush()

        # Собрать метрики по ключам
        kw_metrics = await self._calculate_keyword_metrics(
            analysis.id, period_start, period_end, prev_start, prev_end
        )

        # Сводка
        analysis.summary = await self._build_summary(kw_metrics, period_start, period_end)

        # Проблемы уровня 1
        analysis.problems = await self._detect_problems_l1(kw_metrics)

        # Точки роста
        analysis.opportunities = await self._detect_opportunities_l1(kw_metrics)

        await self.db.commit()
        logger.info(f"Analysis {analysis.id} complete for account {self.account_id}")
        return analysis

    async def _calculate_keyword_metrics(
        self,
        analysis_id: int,
        period_start: datetime,
        period_end: datetime,
        prev_start: datetime,
        prev_end: datetime,
    ) -> list[KeywordMetrics]:
        """Рассчитать метрики по каждому ключу за текущий и предыдущий период"""

        # Текущий период
        stats_q = (
            select(
                KeywordStat.keyword_id,
                func.sum(KeywordStat.clicks).label("clicks"),
                func.sum(KeywordStat.impressions).label("impressions"),
                func.sum(KeywordStat.spend).label("spend"),
                func.avg(KeywordStat.avg_position).label("avg_position"),
                func.avg(KeywordStat.avg_click_position).label("avg_click_position"),
                func.avg(KeywordStat.ctr).label("avg_ctr"),
                func.avg(KeywordStat.avg_cpc).label("avg_cpc"),
                func.avg(KeywordStat.traffic_volume).label("avg_traffic_volume"),
                func.avg(KeywordStat.avg_bid).label("avg_bid"),
            )
            .where(and_(
                KeywordStat.account_id == self.account_id,
                KeywordStat.date >= period_start,
                KeywordStat.date <= period_end,
            ))
            .group_by(KeywordStat.keyword_id)
        )
        stats_result = await self.db.execute(stats_q)
        stats_map = {row.keyword_id: row for row in stats_result}

        # Предыдущий период для сравнения трафика
        prev_q = (
            select(
                KeywordStat.keyword_id,
                func.sum(KeywordStat.clicks).label("clicks"),
                func.avg(KeywordStat.avg_position).label("avg_position"),
                func.avg(KeywordStat.traffic_volume).label("avg_traffic_volume"),
            )
            .where(and_(
                KeywordStat.account_id == self.account_id,
                KeywordStat.date >= prev_start,
                KeywordStat.date <= prev_end,
            ))
            .group_by(KeywordStat.keyword_id)
        )
        prev_result = await self.db.execute(prev_q)
        prev_map = {row.keyword_id: row for row in prev_result}

        # Лиды (если есть)
        leads_q = text("""
            SELECT k.id as keyword_id,
                   COUNT(l.id) as total_leads,
                   COUNT(CASE WHEN l.is_qualified THEN 1 END) as total_sqls,
                   COUNT(CASE WHEN l.is_bad THEN 1 END) as bad_leads
            FROM keywords k
            LEFT JOIN leads l ON l.utm_term ILIKE k.phrase
                AND l.account_id = :account_id
                AND l.created_at BETWEEN :period_start AND :period_end
            WHERE k.account_id = :account_id
            GROUP BY k.id
        """)
        leads_result = await self.db.execute(leads_q, {
            "account_id": self.account_id,
            "period_start": period_start,
            "period_end": period_end,
        })
        leads_map = {row.keyword_id: row for row in leads_result}

        metrics_list = []
        for kw_id, s in stats_map.items():
            clicks = int(s.clicks or 0)
            impressions = int(s.impressions or 0)
            spend = Decimal(str(s.spend or 0))
            prev = prev_map.get(kw_id)
            prev_clicks = int(prev.clicks or 0) if prev else 0
            prev_traffic = float(prev.avg_traffic_volume or 0) if prev else 0
            curr_traffic = float(s.avg_traffic_volume or 0)

            lead_row = leads_map.get(kw_id)
            leads = int(lead_row.total_leads or 0) if lead_row else 0
            sqls = int(lead_row.total_sqls or 0) if lead_row else 0
            bad_leads = int(lead_row.bad_leads or 0) if lead_row else 0

            cr_click_lead = Decimal(str(leads / clicks)) if clicks > 0 and leads > 0 else None
            cr_lead_sql = Decimal(str(sqls / leads)) if leads > 0 else None
            cpl = spend / leads if leads > 0 else None
            cpql = spend / sqls if sqls > 0 else None

            # Падение трафика: клики упали при стабильном или растущем объёме рынка
            traffic_drop = None
            if prev_clicks > 0 and clicks < prev_clicks:
                traffic_drop = round((clicks - prev_clicks) / prev_clicks * 100, 1)

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
                is_significant=clicks >= settings.MIN_CLICKS_KEYWORD,
                recommended_bid=await self._calc_recommended_bid(kw_id, s),
                bid_source="individual" if clicks >= settings.MIN_CLICKS_KEYWORD else "cluster",
            )
            # Сохраняем доп данные в JSON для анализа
            if not hasattr(km, '_extra'):
                km._extra = {}
            km._extra = {
                "avg_position": float(s.avg_position or 0),
                "avg_click_position": float(s.avg_click_position or 0),
                "avg_ctr": float(s.avg_ctr or 0),
                "avg_cpc": float(s.avg_cpc or 0),
                "avg_traffic_volume": curr_traffic,
                "prev_clicks": prev_clicks,
                "prev_traffic": prev_traffic,
                "traffic_drop": traffic_drop,
                "impressions": impressions,
                "bad_leads": bad_leads,
            }
            self.db.add(km)
            metrics_list.append(km)

        await self.db.flush()
        return metrics_list

    async def _calc_recommended_bid(self, kw_id: int, stats) -> Optional[Decimal]:
        """Рекомендованная ставка на основе целевого CPL и CR кластера"""
        kw_result = await self.db.execute(
            select(Keyword).where(Keyword.id == kw_id)
        )
        kw = kw_result.scalar_one_or_none()
        if not kw:
            return None

        from app.models.models import Account
        acc_result = await self.db.execute(
            select(Account).where(Account.id == self.account_id)
        )
        acc = acc_result.scalar_one_or_none()
        if not acc or not acc.target_cpl:
            return None

        # Простая формула: если есть CR — рекомендуем CPL_цель × CR
        # Если нет — рекомендуем на основе позиции
        avg_position = float(stats.avg_position or 0)
        current_bid = float(stats.avg_bid or 0)

        if avg_position > 3 and current_bid > 0:
            # Позиция плохая — рекомендуем поднять на 30%
            return Decimal(str(round(current_bid * 1.3, 2)))
        elif avg_position > 0 and avg_position <= 1.5 and current_bid > 0:
            # Позиция отличная — можно немного снизить
            return Decimal(str(round(current_bid * 0.9, 2)))

        return None

    async def _build_summary(
        self,
        kw_metrics: list[KeywordMetrics],
        period_start: datetime,
        period_end: datetime,
    ) -> dict:
        """Сводные KPI кабинета"""
        total_clicks = sum(km.clicks for km in kw_metrics)
        total_impressions = sum(km._extra.get("impressions", 0) for km in kw_metrics if hasattr(km, '_extra'))
        total_spend = sum(km.spend for km in kw_metrics)
        total_leads = sum(km.leads for km in kw_metrics)
        total_sqls = sum(km.sqls for km in kw_metrics)

        avg_position = 0
        position_count = 0
        for km in kw_metrics:
            if hasattr(km, '_extra') and km._extra.get("avg_position", 0) > 0:
                avg_position += km._extra["avg_position"]
                position_count += 1

        return {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "total_clicks": total_clicks,
            "total_impressions": total_impressions,
            "total_spend": float(total_spend),
            "total_leads": total_leads,
            "total_sqls": total_sqls,
            "ctr": round(total_clicks / total_impressions * 100, 2) if total_impressions > 0 else 0,
            "avg_cpc": round(float(total_spend) / total_clicks, 2) if total_clicks > 0 else 0,
            "cpl": round(float(total_spend) / total_leads, 2) if total_leads > 0 else None,
            "cpql": round(float(total_spend) / total_sqls, 2) if total_sqls > 0 else None,
            "avg_position": round(avg_position / position_count, 2) if position_count > 0 else 0,
            "keywords_analyzed": len(kw_metrics),
            "keywords_significant": sum(1 for km in kw_metrics if km.is_significant),
            "has_crm_data": total_leads > 0,
        }

    async def _detect_problems_l1(self, kw_metrics: list[KeywordMetrics]) -> list[dict]:
        """Проблемы уровня 1 — только рекламные данные"""
        problems = []

        for km in kw_metrics:
            if not hasattr(km, '_extra'):
                continue
            extra = km._extra
            clicks = km.clicks
            impressions = extra.get("impressions", 0)
            avg_pos = extra.get("avg_position", 0)
            avg_click_pos = extra.get("avg_click_position", 0)
            ctr = extra.get("avg_ctr", 0)
            traffic_drop = extra.get("traffic_drop")
            curr_traffic = extra.get("avg_traffic_volume", 0)
            prev_clicks = extra.get("prev_clicks", 0)

            kw_result = await self.db.execute(
                select(Keyword).where(Keyword.id == km.keyword_id)
            )
            kw = kw_result.scalar_one_or_none()
            if not kw:
                continue

            # ── Проблема 1: Низкая позиция показа ────────────────────────
            if avg_pos > 3 and clicks >= 5:
                problems.append({
                    "type": "low_position",
                    "severity": "critical" if avg_pos > 4 else "warning",
                    "keyword_id": km.keyword_id,
                    "phrase": kw.phrase,
                    "clicks": clicks,
                    "avg_position": round(avg_pos, 2),
                    "avg_click_position": round(avg_click_pos, 2) if avg_click_pos else None,
                    "spend": float(km.spend),
                    "description": f"Средняя позиция показа {round(avg_pos, 1)} — объявление показывается внизу страницы.",
                    "action": "Поднять ставку чтобы занять позиции 1–3.",
                    "priority": "today",
                })

            # ── Проблема 2: Падение трафика ───────────────────────────────
            if traffic_drop and traffic_drop < -30 and curr_traffic > 50:
                problems.append({
                    "type": "traffic_drop",
                    "severity": "critical",
                    "keyword_id": km.keyword_id,
                    "phrase": kw.phrase,
                    "clicks": clicks,
                    "prev_clicks": prev_clicks,
                    "traffic_drop_pct": traffic_drop,
                    "traffic_volume": round(curr_traffic),
                    "spend": float(km.spend),
                    "description": f"Клики упали на {abs(traffic_drop):.0f}% при объёме трафика в системе {round(curr_traffic)} ед. Спрос есть — ставок не хватает.",
                    "action": "Поднять ставку до уровня предыдущего периода +20%.",
                    "priority": "today",
                })

            # ── Проблема 3: Показы есть, кликов нет ──────────────────────
            if impressions >= 100 and clicks == 0:
                problems.append({
                    "type": "zero_ctr",
                    "severity": "warning",
                    "keyword_id": km.keyword_id,
                    "phrase": kw.phrase,
                    "impressions": impressions,
                    "clicks": clicks,
                    "description": f"{impressions} показов, 0 кликов. Объявление не привлекает внимание.",
                    "action": "Проверить текст объявления. Возможно несоответствие запросу.",
                    "priority": "this_week",
                })

            # ── Проблема 4: Очень низкий CTR ─────────────────────────────
            if ctr and ctr < 1 and impressions >= 50 and avg_pos <= 3:
                problems.append({
                    "type": "low_ctr",
                    "severity": "warning",
                    "keyword_id": km.keyword_id,
                    "phrase": kw.phrase,
                    "clicks": clicks,
                    "impressions": impressions,
                    "ctr": round(float(ctr), 2),
                    "avg_position": round(avg_pos, 2),
                    "description": f"CTR {round(float(ctr), 2)}% при позиции {round(avg_pos, 1)}. Для топ-3 норма 5–15%.",
                    "action": "Улучшить объявление: добавить УТП, уточнить заголовок.",
                    "priority": "this_week",
                })

            # ── Проблема 5: Позиция клика сильно хуже позиции показа ──────
            if avg_pos > 0 and avg_click_pos > 0 and avg_click_pos > avg_pos + 1.5:
                problems.append({
                    "type": "click_position_gap",
                    "severity": "warning",
                    "keyword_id": km.keyword_id,
                    "phrase": kw.phrase,
                    "avg_position": round(avg_pos, 2),
                    "avg_click_position": round(avg_click_pos, 2),
                    "description": f"Показы на позиции {round(avg_pos, 1)}, клики на {round(avg_click_pos, 1)}. Кликают с нижних позиций.",
                    "action": "Поднять ставку чтобы удерживать позицию при кликах.",
                    "priority": "this_week",
                })

        # Сортировать по расходу
        problems.sort(key=lambda x: x.get("spend", 0), reverse=True)
        return problems[:10]

    async def _detect_opportunities_l1(self, kw_metrics: list[KeywordMetrics]) -> list[dict]:
        """Точки роста уровня 1"""
        opportunities = []

        for km in kw_metrics:
            if not hasattr(km, '_extra'):
                continue
            extra = km._extra
            avg_pos = extra.get("avg_position", 0)
            ctr = extra.get("avg_ctr", 0)

            kw_result = await self.db.execute(
                select(Keyword).where(Keyword.id == km.keyword_id)
            )
            kw = kw_result.scalar_one_or_none()
            if not kw:
                continue

            # Хорошая позиция и высокий CTR — масштабировать
            if avg_pos > 0 and avg_pos <= 2 and float(ctr or 0) > 5 and km.clicks >= 20:
                opportunities.append({
                    "type": "high_ctr_top_position",
                    "keyword_id": km.keyword_id,
                    "phrase": kw.phrase,
                    "clicks": km.clicks,
                    "avg_position": round(avg_pos, 2),
                    "ctr": round(float(ctr), 2),
                    "spend": float(km.spend),
                    "description": f"CTR {round(float(ctr), 2)}% на позиции {round(avg_pos, 1)}. Сильное объявление — масштабировать.",
                    "action": "Добавить похожие ключи. Расширить семантику по этому направлению.",
                })

            # Лиды есть — высокий CR
            if km.cr_click_lead and float(km.cr_click_lead) > settings.CR_HIGH_THRESHOLD:
                opportunities.append({
                    "type": "high_cr",
                    "keyword_id": km.keyword_id,
                    "phrase": kw.phrase,
                    "clicks": km.clicks,
                    "cr": round(float(km.cr_click_lead) * 100, 1),
                    "leads": km.leads,
                    "cpl": float(km.cpl) if km.cpl else None,
                    "description": f"CR {round(float(km.cr_click_lead)*100, 1)}% — конверсионный ключ.",
                    "action": f"Поднять ставку до {float(km.recommended_bid):.0f}₽" if km.recommended_bid else "Поднять ставку для роста трафика.",
                })

        opportunities.sort(key=lambda x: x.get("ctr", x.get("cr", 0)), reverse=True)
        return opportunities[:5]
