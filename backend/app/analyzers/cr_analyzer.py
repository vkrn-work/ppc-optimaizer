"""
CR-анализатор — движок генерации сигналов PPC Optimizer.

Реализует полное дерево диагностики из PPC_METRICS_HANDBOOK:
  Уровень 4: ставки и ключи      → S-001..S-006
  Уровень 3: показы/ЕПК-обвал   → S-010
  Уровень 2: трафик              → S-020
  Уровень 6: поведение (Метрика) → S-040..S-043
  Уровень 7: точки роста         → S-050

Все сигналы содержат:
  signal_id, type, severity, priority, layer,
  entity_type/id/name, problem, hypothesis,
  recommendation, expected_outcome, calculation_logic,
  supported_metrics, metric_value
"""
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional
import logging
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from collections import defaultdict

from app.models.models import (
    Keyword, KeywordStat, Campaign, AdGroup,
    KeywordMetrics, AnalysisResult, Account, MetrikaSnapshot
)
from app.core.config import settings

logger = logging.getLogger(__name__)

# ─── Пороговые значения ─────────────────────────────────────────────────────
# Позиции
POS_CRITICAL            = 4.0
POS_WARNING             = 3.0
POS_GAP_WARNING         = 1.5   # разрыв позиция_клика − позиция_показа

# Трафик
TRAFFIC_VOL_THRESHOLD   = 50
TRAFFIC_DROP_FACTOR     = 0.60  # клики упали > 40%

# CTR (поиск B2B)
CTR_ZERO_MIN_IMPRESSIONS = 100
CTR_LOW_THRESHOLD        = 1.0
CTR_SPIKE_FACTOR         = 1.4  # рост CPC > 40%

# Статистическая значимость
MIN_CLICKS_KW           = 30
MIN_CLICKS_GROUP        = 50
MIN_CLICKS_CAMPAIGN     = 100

# ЕПК-обвал
EPK_COLLAPSE_MIN_KWS    = 5     # минимум ключей
EPK_COLLAPSE_BID_DROP   = 0.50  # ставка упала > 50%

# Расход без конверсий
SPEND_NO_CONV_CLICKS    = 30
SPEND_NO_CONV_MULT      = 3.0   # > 3× target_cpl

# Поведение (Метрика)
BOUNCE_RATE_WARNING     = 60.0
BOUNCE_RATE_CRITICAL    = 75.0
PAGE_DEPTH_LOW          = 1.3
DURATION_LOW_SEC        = 30


class CRAnalyzer:
    def __init__(self, db: AsyncSession, account_id: int):
        self.db         = db
        self.account_id = account_id

    async def run_full_analysis(self, period_days: int = 28) -> AnalysisResult:
        period_end   = datetime.utcnow()
        period_start = period_end - timedelta(days=period_days)
        prev_end     = period_start
        prev_start   = prev_end - timedelta(days=period_days)

        analysis = AnalysisResult(
            account_id=self.account_id,
            period_start=period_start,
            period_end=period_end,
        )
        self.db.add(analysis)
        await self.db.flush()

        curr_stats = await self._agg_stats(period_start, period_end)
        prev_stats = await self._agg_stats(prev_start, prev_end)

        logger.info(
            f"Analysis {analysis.id}: {len(curr_stats)} keywords"
            f" (account_id={self.account_id},"
            f" {period_start.date()}..{period_end.date()})"
        )

        acc_result = await self.db.execute(
            select(Account).where(Account.id == self.account_id)
        )
        account    = acc_result.scalar_one_or_none()
        target_cpl = float(getattr(account, 'target_cpl',  None) or 2000)

        metrika_data = await self._get_latest_metrika()

        signals      = []
        problems     = []
        opportunities = []
        total_clicks = 0
        total_impressions = 0
        total_spend  = 0.0
        keywords_analyzed = 0

        camp_cache: dict[int, Campaign] = {}

        for kw_id, s in curr_stats.items():
            clicks       = int(s.get("clicks") or 0)
            impressions  = int(s.get("impressions") or 0)
            spend        = float(s.get("spend") or 0)
            avg_pos      = float(s.get("avg_position") or 0)
            avg_cpos     = float(s.get("avg_click_position") or 0)
            ctr          = float(s.get("ctr") or 0)
            avg_cpc      = float(s.get("avg_cpc") or 0)
            traffic_vol  = float(s.get("traffic_volume") or 0)
            avg_bid      = float(s.get("avg_bid") or 0)

            prev         = prev_stats.get(kw_id, {})
            prev_clicks  = int(prev.get("clicks") or 0)
            prev_bid     = float(prev.get("avg_bid") or 0)

            total_clicks      += clicks
            total_impressions += impressions
            total_spend       += spend
            keywords_analyzed += 1

            kw_result = await self.db.execute(
                select(Keyword).where(Keyword.id == kw_id)
            )
            kw = kw_result.scalar_one_or_none()
            if not kw:
                continue
            phrase = kw.phrase

            if kw.ad_group_id not in camp_cache:
                c_result = await self.db.execute(
                    select(Campaign)
                    .join(AdGroup, AdGroup.campaign_id == Campaign.id)
                    .where(AdGroup.id == kw.ad_group_id)
                )
                camp_cache[kw.ad_group_id] = c_result.scalar_one_or_none()
            campaign  = camp_cache.get(kw.ad_group_id)
            is_manual = not (campaign and campaign.strategy_type == "AUTO")
            current_bid = float(kw.current_bid or 0)
            if current_bid == 0 and avg_bid > 0:
                current_bid = avg_bid

            # ── S-001: Низкая позиция показа ──────────────────────────────
            if avg_pos > POS_WARNING and clicks >= 3 and is_manual:
                severity = "critical" if avg_pos > POS_CRITICAL else "warning"
                mult     = 1.4 if severity == "critical" else 1.3
                rec_bid  = round(current_bid * mult, 2)
                p = {
                    "signal_id": f"S-001-{kw_id}",
                    "type": "low_position",
                    "severity": severity,
                    "priority": "today" if severity == "critical" else "this_week",
                    "layer": "bid_keyword",
                    "keyword_id": kw_id, "phrase": phrase,
                    "metric_value": round(avg_pos, 2),
                    "description": f"Позиция показа {avg_pos:.1f} — объявление внизу страницы",
                    "hypothesis": "Ставка ниже рыночной, ключ проигрывает аукцион",
                    "action": f"Поднять ставку с {current_bid:.0f}₽ до {rec_bid:.0f}₽ (+{int((mult-1)*100)}%)",
                    "expected_outcome": "Позиция 1.5–2.5, рост кликов 30–50%",
                    "calculation_logic": f"{current_bid:.0f} × {mult} = {rec_bid:.0f}",
                    "recommended_bid": rec_bid,
                    "clicks": clicks, "spend": round(spend, 2),
                    "traffic_volume": round(traffic_vol),
                }
                signals.append(p); problems.append(p)

            # ── S-002: Падение трафика при стабильном спросе ──────────────
            if (prev_clicks > 5
                    and clicks < prev_clicks * TRAFFIC_DROP_FACTOR
                    and traffic_vol > TRAFFIC_VOL_THRESHOLD):
                drop_pct = round((clicks - prev_clicks) / prev_clicks * 100, 1)
                bid_dropped = prev_bid > 0 and avg_bid < prev_bid * 0.8
                rec_bid = round(current_bid * 1.3, 2)
                p = {
                    "signal_id": f"S-002-{kw_id}",
                    "type": "traffic_drop",
                    "severity": "critical",
                    "priority": "today",
                    "layer": "bid_keyword",
                    "keyword_id": kw_id, "phrase": phrase,
                    "metric_value": drop_pct,
                    "clicks": clicks, "prev_clicks": prev_clicks,
                    "traffic_volume": round(traffic_vol), "spend": round(spend, 2),
                    "description": (
                        f"Клики упали на {abs(drop_pct):.0f}%"
                        f" (было {prev_clicks}, стало {clicks})."
                        f" Объём трафика {round(traffic_vol)} — спрос есть"
                    ),
                    "hypothesis": (
                        "Ставка снижена алгоритмом ЕПК (ключ без конверсий)"
                        if bid_dropped else
                        "Конкурент поднял ставки или краткосрочный спад"
                    ),
                    "action": f"Поднять ставку до {rec_bid:.0f}₽ (+30%)",
                    "expected_outcome": f"Возврат к {prev_clicks} кликам/период",
                    "calculation_logic": f"{current_bid:.0f} × 1.3 = {rec_bid:.0f}",
                    "recommended_bid": rec_bid,
                }
                signals.append(p); problems.append(p)

            # ── S-003: Нулевой CTR при высоких показах ────────────────────
            if impressions >= CTR_ZERO_MIN_IMPRESSIONS and clicks == 0:
                p = {
                    "signal_id": f"S-003-{kw_id}",
                    "type": "zero_ctr",
                    "severity": "warning",
                    "priority": "this_week",
                    "layer": "bid_keyword",
                    "keyword_id": kw_id, "phrase": phrase,
                    "metric_value": 0.0, "impressions": impressions,
                    "clicks": 0, "spend": round(spend, 2),
                    "description": f"{impressions} показов, 0 кликов. Объявление не привлекает",
                    "hypothesis": "Нерелевантный заголовок или позиция < 5 (никто не доходит)",
                    "action": "Проверить позицию и объявление. Поисковые запросы — нет ли информационного интента",
                    "expected_outcome": "CTR 2–5%",
                    "calculation_logic": f"Clicks=0 при Impressions={impressions}",
                }
                signals.append(p); problems.append(p)

            # ── S-004: Низкий CTR при хорошей позиции ─────────────────────
            if (ctr > 0 and ctr < CTR_LOW_THRESHOLD
                    and impressions >= 50
                    and avg_pos > 0 and avg_pos <= POS_WARNING):
                p = {
                    "signal_id": f"S-004-{kw_id}",
                    "type": "low_ctr",
                    "severity": "warning",
                    "priority": "this_week",
                    "layer": "bid_keyword",
                    "keyword_id": kw_id, "phrase": phrase,
                    "metric_value": round(ctr, 2),
                    "clicks": clicks, "impressions": impressions,
                    "avg_position": round(avg_pos, 2), "spend": round(spend, 2),
                    "description": f"CTR {ctr:.2f}% при позиции {avg_pos:.1f} — норма топ-3: 2–5%",
                    "hypothesis": "Заголовок не цепляет, нет УТП, устаревшее объявление",
                    "action": "A/B: добавить цену, сроки поставки, конкретный стандарт в заголовок",
                    "expected_outcome": "CTR +50–100%, CPC −10–20%",
                    "calculation_logic": f"CTR = {clicks}/{impressions} × 100 = {ctr:.2f}%",
                }
                signals.append(p); problems.append(p)

            # ── S-005: Позиционный разрыв ─────────────────────────────────
            if (avg_pos > 0 and avg_cpos > 0
                    and avg_cpos > avg_pos + POS_GAP_WARNING and clicks >= 5):
                gap = round(avg_cpos - avg_pos, 2)
                p = {
                    "signal_id": f"S-005-{kw_id}",
                    "type": "click_position_gap",
                    "severity": "info",
                    "priority": "this_week",
                    "layer": "bid_keyword",
                    "keyword_id": kw_id, "phrase": phrase,
                    "metric_value": gap,
                    "avg_position": round(avg_pos, 2),
                    "avg_click_position": round(avg_cpos, 2),
                    "clicks": clicks, "spend": round(spend, 2),
                    "description": (
                        f"Показы на позиции {avg_pos:.1f},"
                        f" клики на {avg_cpos:.1f}. Разрыв {gap}"
                    ),
                    "hypothesis": "Объявление непривлекательно на высокой позиции",
                    "action": "Улучшить заголовок или принять позицию 4–5 (где всё равно кликают) и снизить ставку",
                    "expected_outcome": "Снижение разрыва, предсказуемый CTR",
                    "calculation_logic": f"AvgClickPos {avg_cpos:.1f} − AvgImprPos {avg_pos:.1f} = {gap}",
                }
                signals.append(p); problems.append(p)

            # ── S-006: Расход без конверсий ───────────────────────────────
            if (spend > target_cpl * SPEND_NO_CONV_MULT
                    and clicks >= SPEND_NO_CONV_CLICKS):
                rec_bid = round(current_bid * 0.5, 2)
                p = {
                    "signal_id": f"S-006-{kw_id}",
                    "type": "spend_no_conversion",
                    "severity": "critical",
                    "priority": "today",
                    "layer": "bid_keyword",
                    "keyword_id": kw_id, "phrase": phrase,
                    "metric_value": round(spend, 2),
                    "clicks": clicks, "spend": round(spend, 2),
                    "description": (
                        f"Потрачено {spend:.0f}₽ при {clicks} кликах без конверсий."
                        f" Порог: {target_cpl * SPEND_NO_CONV_MULT:.0f}₽"
                    ),
                    "hypothesis": "Нецелевой трафик — информационный интент или нерелевантные поисковые запросы",
                    "action": f"Открыть поисковые запросы. Минус-слова или снизить ставку до {rec_bid:.0f}₽ (−50%)",
                    "expected_outcome": f"Экономия ~{spend:.0f}₽/мес",
                    "calculation_logic": f"spend {spend:.0f} > target_cpl {target_cpl} × {SPEND_NO_CONV_MULT}",
                    "recommended_bid": rec_bid,
                }
                signals.append(p); problems.append(p)

        # ── S-010: ЕПК-обвал ──────────────────────────────────────────────
        epk_sigs = await self._detect_epk_collapse(curr_stats, prev_stats)
        signals.extend(epk_sigs)
        problems.extend(epk_sigs)

        # ── S-020: CPC-спайк ──────────────────────────────────────────────
        cpc_sigs = await self._analyze_cpc_spikes(curr_stats, prev_stats)
        signals.extend(cpc_sigs)
        problems.extend([s for s in cpc_sigs if s["severity"] != "info"])

        # ── S-040..S-043: Поведение (Метрика) ─────────────────────────────
        if metrika_data:
            beh_sigs = self._analyze_behavior_layer(metrika_data)
            signals.extend(beh_sigs)
            problems.extend([s for s in beh_sigs if s["severity"] in ("critical", "warning")])

        # ── S-050: Точки роста ────────────────────────────────────────────
        for kw_id, s in curr_stats.items():
            clicks     = int(s.get("clicks") or 0)
            avg_pos    = float(s.get("avg_position") or 0)
            ctr        = float(s.get("ctr") or 0)
            spend      = float(s.get("spend") or 0)
            avg_bid    = float(s.get("avg_bid") or 0)
            if ctr > 5 and avg_pos > 2.5 and clicks >= 10:
                kw_result = await self.db.execute(
                    select(Keyword).where(Keyword.id == kw_id)
                )
                kw = kw_result.scalar_one_or_none()
                if not kw:
                    continue
                rec_bid = round(avg_bid * 1.3, 2)
                opp = {
                    "signal_id": f"S-050-{kw_id}",
                    "type": "scale_opportunity",
                    "severity": "info",
                    "priority": "scale",
                    "layer": "opportunity",
                    "keyword_id": kw_id,
                    "phrase": kw.phrase,
                    "metric_value": round(ctr, 2),
                    "clicks": clicks, "spend": round(spend, 2),
                    "description": f"CTR {ctr:.1f}% и позиция {avg_pos:.1f} — потенциал роста",
                    "action": f"Поднять ставку до {rec_bid:.0f}₽ для позиции 1–2",
                    "expected_outcome": f"Рост кликов до {int(clicks * 1.4)}/период",
                    "recommended_bid": rec_bid,
                }
                signals.append(opp); opportunities.append(opp)

        # ── Сортировка ────────────────────────────────────────────────────
        sev_ord  = {"critical": 0, "warning": 1, "info": 2}
        prio_ord = {"today": 0, "this_week": 1, "month": 2, "scale": 3}
        problems.sort(key=lambda x: (
            sev_ord.get(x.get("severity", "info"), 99),
            prio_ord.get(x.get("priority", "month"), 99),
            -(x.get("spend") or 0),
        ))
        problems     = problems[:25]
        opportunities = opportunities[:8]

        # ── Агрегация KPI ─────────────────────────────────────────────────
        pos_v  = [float(s["avg_position"]) for s in curr_stats.values()
                  if s.get("avg_position") and float(s["avg_position"]) > 0]
        cpos_v = [float(s["avg_click_position"]) for s in curr_stats.values()
                  if s.get("avg_click_position") and float(s["avg_click_position"]) > 0]
        traf_v = [float(s["traffic_volume"]) for s in curr_stats.values()
                  if s.get("traffic_volume") and float(s["traffic_volume"]) > 0]

        avg_cpc_agg = round(total_spend / total_clicks, 2) if total_clicks > 0 else None
        ctr_agg     = round(total_clicks / total_impressions * 100, 2) if total_impressions > 0 else None

        # Скоринг качества трафика (из Метрики)
        traffic_quality = None
        if metrika_data:
            sm = metrika_data.get("summary", {})
            br  = float(sm.get("bounceRate", 0) or 0)
            dur = float(sm.get("avgVisitDurationSeconds", 0) or 0)
            pd_ = float(sm.get("pageDepth", 0) or 0)
            if br > 0 or dur > 0:
                score = (
                    (1 - br / 100) * 0.4 +
                    min(dur / 180, 1.0) * 0.3 +
                    min(pd_ / 3, 1.0) * 0.2 +
                    min((ctr_agg or 0) / 5.0, 1.0) * 0.1
                )
                traffic_quality = round(min(score / 0.9 * 100, 100), 1)

        analysis.summary = {
            "total_clicks":         total_clicks,
            "total_impressions":    total_impressions,
            "total_spend":          round(total_spend, 2),
            "ctr":                  ctr_agg,
            "avg_cpc":              avg_cpc_agg,
            "avg_position":         round(sum(pos_v)/len(pos_v), 2) if pos_v else None,
            "avg_click_position":   round(sum(cpos_v)/len(cpos_v), 2) if cpos_v else None,
            "avg_traffic_volume":   round(sum(traf_v)/len(traf_v)) if traf_v else None,
            "keywords_analyzed":    keywords_analyzed,
            "problems_found":       len(problems),
            "opportunities_found":  len(opportunities),
            "signals_by_severity": {
                "critical": sum(1 for s in signals if s.get("severity") == "critical"),
                "warning":  sum(1 for s in signals if s.get("severity") == "warning"),
                "info":     sum(1 for s in signals if s.get("severity") == "info"),
            },
            "traffic_quality_score": traffic_quality,
            "has_crm_data":         False,
            "period_days":          period_days,
        }
        analysis.problems      = problems
        analysis.opportunities = opportunities

        await self.db.commit()
        logger.info(
            f"Analysis {analysis.id} done:"
            f" {len(problems)} problems,"
            f" {len(opportunities)} opps,"
            f" {len(signals)} total signals"
        )
        return analysis

    # ─── ЕПК-обвал ──────────────────────────────────────────────────────────

    async def _detect_epk_collapse(
        self, curr_stats: dict, prev_stats: dict
    ) -> list[dict]:
        collapsed_by_camp: dict[int, list] = defaultdict(list)

        for kw_id, s in curr_stats.items():
            curr_bid    = float(s.get("avg_bid") or 0)
            prev        = prev_stats.get(kw_id, {})
            prev_bid    = float(prev.get("avg_bid") or 0)
            curr_vol    = float(s.get("traffic_volume") or 0)
            curr_clicks = int(s.get("clicks") or 0)
            prev_clicks = int(prev.get("clicks") or 0)

            if prev_bid == 0 or curr_bid == 0:
                continue
            if (curr_bid / prev_bid < (1 - EPK_COLLAPSE_BID_DROP)
                    and curr_vol > TRAFFIC_VOL_THRESHOLD
                    and prev_clicks > 0
                    and curr_clicks < prev_clicks * 0.5):
                kw_res = await self.db.execute(
                    select(Keyword).where(Keyword.id == kw_id)
                )
                kw = kw_res.scalar_one_or_none()
                if not kw:
                    continue
                c_res = await self.db.execute(
                    select(Campaign)
                    .join(AdGroup, AdGroup.campaign_id == Campaign.id)
                    .where(AdGroup.id == kw.ad_group_id)
                )
                camp = c_res.scalar_one_or_none()
                if camp:
                    collapsed_by_camp[camp.id].append({
                        "kw_id": kw_id, "phrase": kw.phrase,
                        "prev_bid": round(prev_bid, 2),
                        "curr_bid": round(curr_bid, 2),
                        "prev_clicks": prev_clicks, "curr_clicks": curr_clicks,
                        "traffic_volume": round(curr_vol),
                    })

        result = []
        for camp_id, kws in collapsed_by_camp.items():
            if len(kws) < EPK_COLLAPSE_MIN_KWS:
                continue
            c_res = await self.db.execute(
                select(Campaign).where(Campaign.id == camp_id)
            )
            camp = c_res.scalar_one_or_none()
            camp_name      = camp.name if camp else f"ID={camp_id}"
            lost_clicks    = sum(k["prev_clicks"] - k["curr_clicks"] for k in kws)
            result.append({
                "signal_id": f"S-010-{camp_id}",
                "type": "epk_bid_collapse",
                "severity": "critical",
                "priority": "today",
                "layer": "impression",
                "keyword_id": None, "phrase": camp_name,
                "entity_type": "campaign", "entity_id": camp_id,
                "entity_name": camp_name,
                "metric_value": len(kws),
                "spend": 0,
                "description": (
                    f"ЕПК-обвал: {len(kws)} ключей потеряли ≥50% ставки."
                    f" Потеря ~{lost_clicks} кликов/период"
                ),
                "hypothesis": (
                    "Алгоритм ЕПК автоматически снизил ставки ключей"
                    " без конверсий — системное поведение"
                ),
                "action": (
                    f"1. Восстановить ставки {min(len(kws), 10)} флагманских ключей вручную (+20–30%)."
                    " 2. Рассмотреть перевод на ТГК с ручными ставками."
                    " 3. Подключить CRM-данные чтобы алгоритм не наказывал конверсионные ключи"
                ),
                "expected_outcome": f"Восстановление ~{lost_clicks} кликов/период",
                "calculation_logic": (
                    f"{len(kws)} ключей с bid_drop > 50%"
                    f" и clicks_drop > 50% при traffic_vol > {TRAFFIC_VOL_THRESHOLD}"
                ),
                "collapsed_keywords": kws[:5],
            })
        return result

    # ─── CPC-спайк ───────────────────────────────────────────────────────────

    async def _analyze_cpc_spikes(
        self, curr_stats: dict, prev_stats: dict
    ) -> list[dict]:
        result = []
        for kw_id, s in curr_stats.items():
            avg_cpc  = float(s.get("avg_cpc") or 0)
            clicks   = int(s.get("clicks") or 0)
            spend    = float(s.get("spend") or 0)
            prev     = prev_stats.get(kw_id, {})
            prev_cpc = float(prev.get("avg_cpc") or 0)

            if prev_cpc > 0 and avg_cpc > prev_cpc * CTR_SPIKE_FACTOR and clicks >= 5:
                kw_res = await self.db.execute(
                    select(Keyword).where(Keyword.id == kw_id)
                )
                kw = kw_res.scalar_one_or_none()
                if not kw:
                    continue
                growth = round((avg_cpc - prev_cpc) / prev_cpc * 100, 1)
                result.append({
                    "signal_id": f"S-020-{kw_id}",
                    "type": "cpc_spike",
                    "severity": "warning",
                    "priority": "today",
                    "layer": "traffic",
                    "keyword_id": kw_id, "phrase": kw.phrase,
                    "metric_value": round(avg_cpc, 2),
                    "clicks": clicks, "spend": round(spend, 2),
                    "description": f"CPC вырос на {growth}% (было {prev_cpc:.0f}₽, стало {avg_cpc:.0f}₽)",
                    "hypothesis": (
                        "Конкурент поднял ставки или CTR упал"
                        " (алгоритм штрафует ростом CPC)"
                    ),
                    "action": (
                        "Если позиция улучшилась — OK. "
                        "Если CTR упал — улучшить объявление. "
                        "Если без изменений — конкуренты подняли ставки"
                    ),
                    "expected_outcome": "Нормализация CPC",
                    "calculation_logic": (
                        f"({avg_cpc:.0f} − {prev_cpc:.0f}) / {prev_cpc:.0f} × 100"
                        f" = +{growth}%"
                    ),
                })
        return result

    # ─── Поведение (Метрика) ─────────────────────────────────────────────────

    def _analyze_behavior_layer(self, metrika_data: dict) -> list[dict]:
        result  = []
        summary = metrika_data.get("summary", {})
        if not summary:
            return result

        br     = float(summary.get("bounceRate", 0) or 0)
        pd_    = float(summary.get("pageDepth", 0) or 0)
        dur    = float(summary.get("avgVisitDurationSeconds", 0) or 0)
        visits = int(summary.get("visits", 0) or 0)

        if br > BOUNCE_RATE_WARNING and visits > 20:
            sev = "critical" if br > BOUNCE_RATE_CRITICAL else "warning"
            result.append({
                "signal_id": "S-040",
                "type": "high_bounce_rate",
                "severity": sev,
                "priority": "this_week",
                "layer": "behavior",
                "keyword_id": None, "phrase": "Сайт (Метрика)",
                "metric_value": round(br, 1),
                "spend": 0,
                "description": f"Bounce rate {br:.1f}% — норма для B2B: < 50%",
                "hypothesis": "Объявление не совпадает с посадочной страницей",
                "action": (
                    "Синхронизировать заголовок объявления с H1 лендинга."
                    " Убедиться что марка/стандарт из ключа упомянута на странице"
                ),
                "expected_outcome": "Снижение bounce rate до 40–55%",
                "calculation_logic": f"BounceRate {br:.1f} > {BOUNCE_RATE_WARNING}",
                "bounce_rate": round(br, 1), "visits": visits,
            })

        if pd_ < PAGE_DEPTH_LOW and visits > 20:
            result.append({
                "signal_id": "S-041",
                "type": "low_page_depth",
                "severity": "info",
                "priority": "this_week",
                "layer": "behavior",
                "keyword_id": None, "phrase": "Сайт (Метрика)",
                "metric_value": round(pd_, 2),
                "spend": 0,
                "description": f"Глубина просмотра {pd_:.1f} стр. — норма: 1.5–3",
                "hypothesis": "Нет перелинковки по ассортименту",
                "action": "Добавить блок 'Похожие стандарты', навигацию по маркам стали",
                "expected_outcome": "Рост глубины до 1.8–2.5",
                "calculation_logic": f"PageDepth {pd_:.1f} < {PAGE_DEPTH_LOW}",
                "page_depth": round(pd_, 2), "visits": visits,
            })

        if dur < DURATION_LOW_SEC and visits > 20 and dur > 0:
            result.append({
                "signal_id": "S-042",
                "type": "low_visit_duration",
                "severity": "info",
                "priority": "this_week",
                "layer": "behavior",
                "keyword_id": None, "phrase": "Сайт (Метрика)",
                "metric_value": round(dur),
                "spend": 0,
                "description": f"Среднее время на сайте {dur:.0f} сек — норма: > 30 сек",
                "hypothesis": "Посадочная не удерживает, нет конкретного оффера",
                "action": (
                    "Улучшить первый экран: конкретный оффер, таблица стандартов, форма КП"
                ),
                "expected_outcome": "Рост времени до 60–120 сек",
                "calculation_logic": f"AvgDuration {dur:.0f}s < {DURATION_LOW_SEC}s",
                "avg_duration_sec": round(dur), "visits": visits,
            })

        devices     = metrika_data.get("devices", [])
        mob  = next((d for d in devices if "mobile" in str(d.get("deviceCategory", "")).lower()), None)
        desk = next((d for d in devices if "desktop" in str(d.get("deviceCategory", "")).lower()), None)
        if mob and desk:
            mob_br  = float(mob.get("bounceRate", 0) or 0)
            desk_br = float(desk.get("bounceRate", 0) or 0)
            mob_vis = int(mob.get("visits", 0) or 0)
            if mob_br > desk_br + 20 and mob_vis > 10:
                result.append({
                    "signal_id": "S-043",
                    "type": "mobile_quality_issue",
                    "severity": "warning",
                    "priority": "this_week",
                    "layer": "behavior",
                    "keyword_id": None, "phrase": "Мобильный трафик",
                    "metric_value": round(mob_br, 1),
                    "spend": 0,
                    "description": (
                        f"Мобильный bounce rate {mob_br:.1f}%"
                        f" vs desktop {desk_br:.1f}% (+{mob_br - desk_br:.1f}%)"
                    ),
                    "hypothesis": "Сайт плохо адаптирован для мобильных",
                    "action": (
                        "Проверить адаптивность."
                        " Рассмотреть корректировку ставок −50% на mobile"
                        " (B2B-снабженцы работают с ПК)"
                    ),
                    "expected_outcome": "Снижение расхода на нецелевой мобильный трафик",
                    "calculation_logic": (
                        f"mob_bounce {mob_br:.1f} − desk_bounce {desk_br:.1f}"
                        f" = +{mob_br - desk_br:.1f}%"
                    ),
                    "mobile_bounce": round(mob_br, 1),
                    "desktop_bounce": round(desk_br, 1),
                    "mobile_visits": mob_vis,
                })

        return result

    # ─── Хелперы ─────────────────────────────────────────────────────────────

    async def _get_latest_metrika(self) -> Optional[dict]:
        try:
            res = await self.db.execute(
                select(MetrikaSnapshot)
                .where(MetrikaSnapshot.account_id == self.account_id)
                .order_by(MetrikaSnapshot.date.desc())
                .limit(1)
            )
            snap = res.scalar_one_or_none()
            return snap.data if snap else None
        except Exception as e:
            logger.warning(f"Metrika snapshot load failed: {e}")
            return None

    async def _agg_stats(
        self, date_from: datetime, date_to: datetime
    ) -> dict:
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
                # Расширенные поля
                func.avg(KeywordStat.bounce_rate).label("bounce_rate"),
                func.sum(KeywordStat.sessions).label("sessions"),
                func.avg(KeywordStat.weighted_ctr).label("weighted_ctr"),
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
