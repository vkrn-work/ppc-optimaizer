"""
CR-анализатор — уровень 1.
Читает данные из БД и генерирует проблемы и точки роста.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional
import logging
from sqlalchemy import select, func, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    Keyword, KeywordStat, Campaign, AdGroup,
    KeywordMetrics, AnalysisResult, Account
)
from app.core.config import settings

logger = logging.getLogger(__name__)


class CRAnalyzer:
    def __init__(self, db: AsyncSession, account_id: int):
        self.db = db
        self.account_id = account_id

    async def run_full_analysis(self, period_days: int = 28) -> AnalysisResult:
        period_end = datetime.utcnow()
        period_start = period_end - timedelta(days=period_days)
        prev_end = period_start
        prev_start = prev_end - timedelta(days=period_days)

        analysis = AnalysisResult(
            account_id=self.account_id,
            period_start=period_start,
            period_end=period_end,
        )
        self.db.add(analysis)
        await self.db.flush()

        # Агрегировать статистику по ключам за текущий период
        curr_stats = await self._agg_stats(period_start, period_end)
        prev_stats = await self._agg_stats(prev_start, prev_end)

        logger.info(
            f"Analysis {analysis.id}: {len(curr_stats)} keywords with data "
            f"(account_id={self.account_id}, period={period_start.date()}..{period_end.date()})"
        )
        if not curr_stats:
            # Проверим есть ли вообще данные в keyword_stats
            from sqlalchemy import text as sql_text
            check = await self.db.execute(sql_text(
                "SELECT COUNT(*) as cnt, MIN(date) as min_date, MAX(date) as max_date "
                "FROM keyword_stats WHERE account_id = :aid"
            ), {"aid": self.account_id})
            row = check.one()
            logger.warning(
                f"No stats found for period! "
                f"Total rows in keyword_stats for account {self.account_id}: {row.cnt}, "
                f"date range: {row.min_date} - {row.max_date}"
            )

        problems = []
        opportunities = []
        total_clicks = 0
        total_impressions = 0
        total_spend = 0.0
        keywords_analyzed = 0

        for kw_id, s in curr_stats.items():
            clicks = int(s["clicks"] or 0)
            impressions = int(s["impressions"] or 0)
            spend = float(s["spend"] or 0)
            avg_pos = float(s["avg_position"] or 0)
            avg_click_pos = float(s["avg_click_position"] or 0)
            ctr = float(s["ctr"] or 0)
            avg_cpc = float(s["avg_cpc"] or 0)
            traffic_vol = float(s["traffic_volume"] or 0)
            avg_bid = float(s["avg_bid"] or 0)

            prev = prev_stats.get(kw_id, {})
            prev_clicks = int(prev.get("clicks") or 0)
            prev_traffic = float(prev.get("traffic_volume") or 0)

            total_clicks += clicks
            total_impressions += impressions
            total_spend += spend
            keywords_analyzed += 1

            # Загрузить фразу ключа
            kw_result = await self.db.execute(
                select(Keyword).where(Keyword.id == kw_id)
            )
            kw = kw_result.scalar_one_or_none()
            if not kw:
                continue
            phrase = kw.phrase

            # Загрузить стратегию кампании
            camp_result = await self.db.execute(
                select(Campaign).join(AdGroup, AdGroup.campaign_id == Campaign.id)
                    .where(AdGroup.id == kw.ad_group_id)
            )
            campaign = camp_result.scalar_one_or_none()
            is_manual = True  # по умолчанию анализируем
            if campaign and campaign.strategy_type == "AUTO":
                is_manual = False

            # ── Проблема 1: Низкая позиция показа ────────────────────────
            if avg_pos > 3.0 and clicks >= 5 and is_manual:
                problems.append({
                    "type": "low_position",
                    "severity": "critical" if avg_pos > 4 else "warning",
                    "keyword_id": kw_id,
                    "phrase": phrase,
                    "metric_value": round(avg_pos, 2),
                    "clicks": clicks,
                    "spend": round(spend, 2),
                    "description": f"Позиция показа {round(avg_pos, 1)} — объявление внизу страницы.",
                    "action": "Поднять ставку чтобы занять позиции 1–3.",
                    "recommended_bid": round(avg_bid * 1.3, 2) if avg_bid > 0 else None,
                    "priority": "today",
                })

            # ── Проблема 2: Падение трафика ───────────────────────────────
            if prev_clicks > 10 and clicks < prev_clicks * 0.6 and traffic_vol > 50:
                drop_pct = round((clicks - prev_clicks) / prev_clicks * 100, 1)
                problems.append({
                    "type": "traffic_drop",
                    "severity": "critical",
                    "keyword_id": kw_id,
                    "phrase": phrase,
                    "metric_value": drop_pct,
                    "clicks": clicks,
                    "prev_clicks": prev_clicks,
                    "traffic_volume": round(traffic_vol),
                    "spend": round(spend, 2),
                    "description": f"Клики упали на {abs(drop_pct):.0f}% (было {prev_clicks}, стало {clicks}). Объём трафика в системе {round(traffic_vol)} — спрос есть.",
                    "action": f"Поднять ставку до {round(avg_bid * 1.3, 2)}₽ (+30%)." if avg_bid > 0 else "Поднять ставку.",
                    "recommended_bid": round(avg_bid * 1.3, 2) if avg_bid > 0 else None,
                    "priority": "today",
                })

            # ── Проблема 3: Много показов, нет кликов ────────────────────
            if impressions >= 100 and clicks == 0:
                problems.append({
                    "type": "zero_ctr",
                    "severity": "warning",
                    "keyword_id": kw_id,
                    "phrase": phrase,
                    "metric_value": 0,
                    "impressions": impressions,
                    "clicks": 0,
                    "spend": round(spend, 2),
                    "description": f"{impressions} показов и 0 кликов. Объявление не привлекает.",
                    "action": "Проверить текст объявления. Возможно несоответствие запросу.",
                    "priority": "this_week",
                })

            # ── Проблема 4: Низкий CTR на хорошей позиции ────────────────
            if ctr > 0 and ctr < 1.0 and impressions >= 50 and avg_pos > 0 and avg_pos <= 3:
                problems.append({
                    "type": "low_ctr",
                    "severity": "warning",
                    "keyword_id": kw_id,
                    "phrase": phrase,
                    "metric_value": round(ctr, 2),
                    "clicks": clicks,
                    "impressions": impressions,
                    "avg_position": round(avg_pos, 2),
                    "spend": round(spend, 2),
                    "description": f"CTR {round(ctr, 2)}% при позиции {round(avg_pos, 1)}. Норма для топ-3: 5–15%.",
                    "action": "Улучшить объявление: добавить УТП, уточнить заголовок, добавить расширения.",
                    "priority": "this_week",
                })

            # ── Проблема 5: Gap между позицией показа и клика ─────────────
            if avg_pos > 0 and avg_click_pos > 0 and avg_click_pos > avg_pos + 1.5 and clicks >= 5:
                problems.append({
                    "type": "click_position_gap",
                    "severity": "warning",
                    "keyword_id": kw_id,
                    "phrase": phrase,
                    "metric_value": round(avg_click_pos - avg_pos, 2),
                    "avg_position": round(avg_pos, 2),
                    "avg_click_position": round(avg_click_pos, 2),
                    "clicks": clicks,
                    "spend": round(spend, 2),
                    "description": f"Показы на позиции {round(avg_pos, 1)}, клики на {round(avg_click_pos, 1)}. Позиция при кликах хуже.",
                    "action": "Поднять ставку чтобы удерживать позицию при кликах.",
                    "priority": "this_week",
                })

            # ── Возможность: высокий CTR — масштабировать ────────────────
            if ctr > 5 and avg_pos <= 2 and clicks >= 15:
                opportunities.append({
                    "type": "scale_high_ctr",
                    "keyword_id": kw_id,
                    "phrase": phrase,
                    "ctr": round(ctr, 2),
                    "avg_position": round(avg_pos, 2),
                    "clicks": clicks,
                    "spend": round(spend, 2),
                    "description": f"CTR {round(ctr, 2)}% на позиции {round(avg_pos, 1)}. Сильное объявление.",
                    "action": "Расширить семантику по этому направлению.",
                })

        # Сортировка: критичные сначала, потом по расходу
        problems.sort(key=lambda x: (0 if x["severity"] == "critical" else 1, -x.get("spend", 0)))
        problems = problems[:20]
        opportunities = opportunities[:5]

        # Сводка
        analysis.summary = {
            "total_clicks": total_clicks,
            "total_impressions": total_impressions,
            "total_spend": round(total_spend, 2),
            "ctr": round(total_clicks / total_impressions * 100, 2) if total_impressions > 0 else 0,
            "avg_cpc": round(total_spend / total_clicks, 2) if total_clicks > 0 else 0,
            "keywords_analyzed": keywords_analyzed,
            "problems_found": len(problems),
            "opportunities_found": len(opportunities),
            "has_crm_data": False,
            "period_days": 28,
        }
        analysis.problems = problems
        analysis.opportunities = opportunities

        await self.db.commit()
        logger.info(
            f"Analysis {analysis.id} complete: "
            f"{len(problems)} problems, {len(opportunities)} opportunities"
        )
        return analysis

    async def _agg_stats(self, date_from: datetime, date_to: datetime) -> dict:
        """Агрегировать статистику по ключам за период"""
        q = (
            select(
                KeywordStat.keyword_id,
                func.sum(KeywordStat.clicks).label("clicks"),
                func.sum(KeywordStat.impressions).label("impressions"),
                func.sum(KeywordStat.spend).label("spend"),
                func.avg(KeywordStat.avg_position).label("avg_position"),
                func.avg(KeywordStat.avg_click_position).label("avg_click_position"),
                func.avg(KeywordStat.ctr).label("ctr"),
                func.avg(KeywordStat.avg_cpc).label("avg_cpc"),
                func.avg(KeywordStat.traffic_volume).label("traffic_volume"),
                func.avg(KeywordStat.avg_bid).label("avg_bid"),
            )
            .where(and_(
                KeywordStat.account_id == self.account_id,
                KeywordStat.date >= date_from,
                KeywordStat.date <= date_to,
            ))
            .group_by(KeywordStat.keyword_id)
        )
        result = await self.db.execute(q)
        return {row.keyword_id: row._asdict() for row in result}
