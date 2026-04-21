"""
CR-анализатор v2.0 — Движок "Senior PPC" для B2B низкочастотной ниши.

Ключевые отличия от v1.2:
  - Динамические базовые линии (медианы за 4 недели) вместо жестких констант.
  - Фильтр выходных дней (Пн-Пт) для расчета трендов.
  - Кластерный анализ: агрегация проблем на уровень кампаний.
  - Извлечение поведенческих метрик по ключам из MetrikaSnapshot JSON.
  - Новые сигналы: падение показов, разрыв клики/визиты, тренд 3-х дней.
"""
import json
import logging
import statistics
from datetime import datetime, timedelta, date
from typing import Optional
from collections import defaultdict

from sqlalchemy import select, and_, extract
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    Keyword, KeywordStat, Campaign, AdGroup,
    AnalysisResult, Account, MetrikaSnapshot
)

logger = logging.getLogger(__name__)

# ─── Настройки по умолчанию (переопределяются через Account.analysis_config) ──
DEFAULT_CONFIG = {
    "target_cpl": 2000.0,
    "drop_pct_critical": 0.50,      # Падение > 50% от базовой линии
    "drop_pct_warning": 0.35,       # Падение > 35%
    "min_baseline_clicks": 2.0,     # Если медиана ключа < 2 кликов/нед - не сигнализируем просадку (шум)
    "min_baseline_impressions": 10, # Для показов
    "short_trend_days": 3,          # Проверка тренда за 3 рабочих дня
    "click_visit_gap_pct": 0.20,    # Допустимый разрыв кликов и визитов (20%)
}


def _is_workday(d: date) -> bool:
    """Проверка, что день рабочий (Пн-Пт)"""
    return d.weekday() < 5


class CRAnalyzer:
    def __init__(self, db: AsyncSession, account_id: int):
        self.db = db
        self.account_id = account_id

    async def run_full_analysis(self, period_days: int = 28) -> AnalysisResult:
        # Периоды: текущий и предыдущий (для расчета базовой линии)
        period_end = datetime.utcnow().date()
        period_start = period_end - timedelta(days=period_days)
        baseline_start = period_start - timedelta(days=28) # 4 недели до текущего

        analysis = AnalysisResult(
            account_id=self.account_id,
            period_start=datetime.combine(period_start, datetime.min.time()),
            period_end=datetime.combine(period_end, datetime.min.time()),
        )
        self.db.add(analysis)
        await self.db.flush()

        # 1. Загружаем настройки кабинета
        acc_result = await self.db.execute(
            select(Account).where(Account.id == self.account_id)
        )
        account = acc_result.scalar_one_or_none()
        config = {**DEFAULT_CONFIG}
        if account and account.analysis_config:
            try:
                config.update(json.loads(account.analysis_config))
            except Exception:
                pass
        target_cpl = float(config.get("target_cpl", 2000))

        # 2. Собираем сырые дневные данные за baseline (4 недели) + текущий период
        # Используем только рабочие дни для расчета медианы
        raw_daily_stats = await self._get_raw_daily_stats(baseline_start, period_end)
        
        # 3. Считаем динамические базовые линии (медианы) по каждому ключу
        baselines = self._calculate_baselines(raw_daily_stats, baseline_start, period_start)

        # 4. Агрегируем текущий период для финальных цифр
        curr_stats = await self._agg_stats(period_start, period_end)

        # 5. Загружаем данные Метрики по ключам (поведение)
        metrika_kw_data = await self._get_metrika_keyword_data()

        # Кэш для кампаний
        camp_cache = {}
        async def get_campaign(kw_id):
            if kw_id not in camp_cache:
                kw_res = await self.db.execute(select(Keyword).where(Keyword.id == kw_id))
                kw = kw_res.scalar_one_or_none()
                if kw:
                    c_res = await self.db.execute(
                        select(Campaign).join(AdGroup, AdGroup.campaign_id == Campaign.id)
                        .where(AdGroup.id == kw.ad_group_id)
                    )
                    camp_cache[kw_id] = c_res.scalar_one_or_none()
                else:
                    camp_cache[kw_id] = None
            return camp_cache[kw_id]

        signals = []
        problems = []
        opportunities = []
        
        # Структура для кластерного анализа: campaign_id -> list[kw_signals]
        cluster_drops = defaultdict(list)
        total_keywords_in_camp = defaultdict(int)

        for kw_id, curr in curr_stats.items():
            kw_res = await self.db.execute(select(Keyword).where(Keyword.id == kw_id))
            kw = kw_res.scalar_one_or_none()
            if not kw: continue
            
            phrase = kw.phrase
            campaign = await get_campaign(kw_id)
            if not campaign: continue
            
            is_manual = campaign.strategy_type != "AUTO"
            camp_id = campaign.id
            total_keywords_in_camp[camp_id] += 1

            bl = baselines.get(kw_id, {})
            curr_clicks = int(curr.get("clicks") or 0)
            curr_impr = int(curr.get("impressions") or 0)
            curr_spend = float(curr.get("spend") or 0)
            curr_pos = float(curr.get("avg_position") or 0)
            curr_ctr = float(curr.get("ctr") or 0)
            curr_bid = float(curr.get("avg_bid") or 0) or float(kw.current_bid or 0)
            
            # Берем METRIKA поведенку по ключу
            m_data = metrika_kw_data.get(phrase, {})
            m_bounce = float(m_data.get("bounceRate") or 0)
            m_depth = float(m_data.get("pageDepth") or 0)
            m_visits = int(m_data.get("visits") or 0)

            bl_clicks = bl.get("med_clicks", 0)
            bl_impr = bl.get("med_impressions", 0)

            # ── S-001: Низкая позиция показа ──────────────────────────────
            if curr_pos > 3.0 and curr_clicks >= 1 and is_manual:
                severity = "critical" if curr_pos > 4.0 else "warning"
                mult = 1.5 if severity == "critical" else 1.3
                rec_bid = round(curr_bid * mult, 2) if curr_bid > 0 else 0
                p = self._make_signal(f"S-001-{kw_id}", "low_position", severity, "today", "bid_keyword",
                                      kw_id, phrase, curr_pos,
                                      f"Позиция {curr_pos:.1f}. Объявление не в топе.",
                                      "Ставка ниже рыночной.", f"Поднять ставку до {rec_bid:.0f}₽",
                                      f"Позиция 1.5-2.5", recommended_bid=rec_bid, clicks=curr_clicks)
                signals.append(p); problems.append(p)

            # ── S-007: Падение показов (Динамическое) ────────────────────
            if bl_impr >= config["min_baseline_impressions"] and curr_impr > 0:
                drop_impr = (curr_impr - bl_impr) / bl_impr
                if drop_impr < -config["drop_pct_warning"]:
                    cluster_drops[camp_id].append({"type": "impressions", "kw_id": kw_id, "phrase": phrase, "drop": drop_impr})
                    if drop_impr < -config["drop_pct_critical"]:
                        p = self._make_signal(f"S-007-{kw_id}", "impression_drop", "critical", "today", "impression",
                                              kw_id, phrase, round(drop_impr * 100, 1),
                                              f"Показы упали на {abs(drop_impr)*100:.0f}% (было ~{bl_impr}, стало {curr_impr}).",
                                              "Конкуренты подняли ставки ИЛИ упал объём трафика (AvgTrafficVolume).",
                                              "Срочно проверить позицию и повысить ставку.", "Восстановление показов")
                        signals.append(p); problems.append(p)

            # ── S-002: Падение трафика (Динамическое) ─────────────────────
            if bl_clicks >= config["min_baseline_clicks"] and curr_clicks > 0:
                drop_clicks = (curr_clicks - bl_clicks) / bl_clicks
                if drop_clicks < -config["drop_pct_warning"]:
                    cluster_drops[camp_id].append({"type": "clicks", "kw_id": kw_id, "phrase": phrase, "drop": drop_clicks})
                    if drop_clicks < -config["drop_pct_critical"]:
                        rec_bid = round(curr_bid * 1.3, 2) if curr_bid > 0 else 0
                        p = self._make_signal(f"S-002-{kw_id}", "traffic_drop", "critical", "today", "bid_keyword",
                                              kw_id, phrase, round(drop_clicks * 100, 1),
                                              f"Клики упали на {abs(drop_clicks)*100:.0f}% относительно нормы ({bl_clicks:.1f} -> {curr_clicks}).",
                                              "ЕПК снизил ставку ИЛИ конкурент перебил.",
                                              f"Поднять ставку до {rec_bid:.0f}₽", "Возврат к норме кликов",
                                              recommended_bid=rec_bid, clicks=curr_clicks)
                        signals.append(p); problems.append(p)

            # ── S-003: Нулевой CTR (Адаптивный) ───────────────────────────
            vol_tier = "HIGH" if bl_impr >= 50 else ("MED" if bl_impr >= 20 else "LOW")
            min_impr_for_zero = {"HIGH": 50, "MED": 20, "LOW": 10}.get(vol_tier, 10)
            
            if curr_impr >= min_impr_for_zero and curr_clicks == 0:
                p = self._make_signal(f"S-003-{kw_id}", "zero_ctr", "warning", "this_week", "bid_keyword",
                                      kw_id, phrase, 0,
                                      f"{curr_impr} показов, 0 кликов.",
                                      "Позиция > 5 ИЛИ заголовок полностью не релевантен запросу.",
                                      "Проверить поисковые запросы. Минус-слова или повысить ставку.", "CTR 2-5%")
                signals.append(p); problems.append(p)

            # ── S-008: Разрыв Clicks vs Sessions (Сайт не доезжает) ──────
            if curr_clicks >= 5 and m_visits > 0:
                gap = (curr_clicks - m_visits) / curr_clicks
                if gap > config["click_visit_gap_pct"]:
                    p = self._make_signal(f"S-008-{kw_id}", "click_visit_gap", "warning", "today", "traffic",
                                          kw_id, phrase, round(gap * 100, 1),
                                          f"Кликов: {curr_clicks}, Визитов в Метрике: {m_visits}. Разрыв {gap*100:.0f}%.",
                                          "Сайт недоступен часть времени, медленная загрузка или блокировщики.",
                                          "Проверить доступность сайта и скорость загрузки.", "Снижение разрыва до 10%")
                    signals.append(p); problems.append(p)

            # ── S-040 (по ключу): Высокий Bounce Rate из Метрики ─────────
            if m_bounce > 60 and m_visits >= 5:
                sev = "critical" if m_bounce > 75 else "warning"
                p = self._make_signal(f"S-040-{kw_id}", "high_bounce_rate_kw", sev, "this_week", "behavior",
                                      kw_id, phrase, round(m_bounce, 1),
                                      f"Bounce rate по ключу {m_bounce:.0f}% (визитов: {m_visits}).",
                                      "Посадочная страница не отвечает на интент запроса.",
                                      "Проверить релевантность лендинга фразе.", "Снижение до 50%")
                signals.append(p); problems.append(p)

        # ── КЛАСТЕРНЫЙ АНАЛИЗ (Кампании) ────────────────────────────────
        for camp_id, drops in cluster_drops.items():
            camp_res = await self.db.execute(select(Campaign).where(Campaign.id == camp_id))
            camp = camp_res.scalar_one_or_none()
            if not camp: continue
            
            total_kw = total_keywords_in_camp[camp_id]
            drop_pct_of_camp = len(drops) / total_kw if total_kw > 0 else 0
            
            # Если просело больше 40% ключей в кампании - это системная проблема
            if drop_pct_of_camp >= 0.4:
                drop_type = "показов" if drops[0]["type"] == "impressions" else "кликов"
                p = self._make_signal(f"S-010-{camp_id}", "cluster_drop", "critical", "today", "impression",
                                      None, camp.name, len(drops),
                                      f"Просадка по {drop_type}: {len(drops)} из {total_kw} ключей упали.",
                                      "Системная проблема: бюджет исчерпан, ЕПК обвал ИЛИ конкурент поднял ставки на всю нишу.",
                                      "Проверить дневной бюджет кампании и общие ставки.", "Восстановление кластера",
                                      entity_type="campaign", entity_id=camp_id)
                signals.append(p); problems.insert(0, p) # Системные проблемы в начало списка

        # ── КРАТКОСРОЧНЫЙ ТРЕНД (3 рабочих дня) ────────────────────────
        # Проверяем только ключи с нормой > 0.5 клика в день
        recent_days = config["short_trend_days"]
        trend_end = period_end
        trend_start = trend_end - timedelta(days=7) # Ищем 3 рабочих дня в пределах недели
        
        trend_signals = await self._check_short_trend(trend_start, trend_end, recent_days, baselines)
        problems.extend([s for s in trend_signals if s["severity"] == "critical"])
        signals.extend(trend_signals)

        # Очистка и сортировка
        sev_ord = {"critical": 0, "warning": 1, "info": 2}
        problems = sorted(problems, key=lambda x: (sev_ord.get(x.get("severity"), 99), -(x.get("clicks") or 0)))[:30]

        # Сохранение
        analysis.problems = problems
        analysis.opportunities = opportunities[:5] # Точки роста пока отложены (требуют CRM)
        analysis.summary = {
            "keywords_analyzed": len(curr_stats),
            "problems_found": len(problems),
            "opportunities_found": len(opportunities),
            "signals_by_severity": {
                "critical": sum(1 for s in signals if s.get("severity") == "critical"),
                "warning": sum(1 for s in signals if s.get("severity") == "warning"),
                "info": sum(1 for s in signals if s.get("severity") == "info"),
            },
            "has_crm_data": False,
            "period_days": period_days,
        }
        
        await self.db.commit()
        logger.info(f"Analysis v2.0 {analysis.id}: {len(problems)} problems found.")
        return analysis

    # ─── Вспомогательные методы ──────────────────────────────────────────────

    async def _get_raw_daily_stats(self, start_date: date, end_date: date) -> list:
        """Получаем сырые дневные строки для расчета динамической базы"""
        q = select(KeywordStat).where(and_(
            KeywordStat.account_id == self.account_id,
            KeywordStat.date >= start_date,
            KeywordStat.date <= end_date,
        ))
        result = await self.db.execute(q)
        return result.scalars().all()

    def _calculate_baselines(self, daily_stats: list, bl_start: date, bl_end: date) -> dict:
        """Считаем медианы за базовый период (только Пн-Пт)"""
        grouped = defaultdict(lambda: {"clicks": [], "impressions": []})
        
        for row in daily_stats:
            if not _is_workday(row.date.date()): continue
            if row.date.date() < bl_start or row.date.date() > bl_end: continue
            
            grouped[row.keyword_id]["clicks"].append(int(row.clicks or 0))
            grouped[row.keyword_id]["impressions"].append(int(row.impressions or 0))

        baselines = {}
        for kw_id, data in grouped.items():
            clicks = [c for c in data["clicks"] if c > 0]
            impr = [i for i in data["impressions"] if i > 0]
            
            # Считаем медиану только из дней, когда были клики/показы
            baselines[kw_id] = {
                "med_clicks": statistics.median(clicks) if clicks else 0,
                "med_impressions": statistics.median(impr) if impr else 0,
            }
        return baselines

    async def _agg_stats(self, start_date: date, end_date: date) -> dict:
        """Агрегация за текущий период (средние позиции, суммы кликов)"""
        from sqlalchemy import func
        q = select(
            KeywordStat.keyword_id,
            func.sum(KeywordStat.clicks).label("clicks"),
            func.sum(KeywordStat.impressions).label("impressions"),
            func.sum(KeywordStat.spend).label("spend"),
            func.avg(KeywordStat.avg_position).label("avg_position"),
            func.avg(KeywordStat.ctr).label("ctr"),
            func.avg(KeywordStat.avg_bid).label("avg_bid"),
        ).where(and_(
            KeywordStat.account_id == self.account_id,
            KeywordStat.date >= start_date,
            KeywordStat.date <= end_date,
        )).group_by(KeywordStat.keyword_id)
        
        result = await self.db.execute(q)
        return {row.keyword_id: row._asdict() for row in result}

    async def _get_metrika_keyword_data(self) -> dict:
        """Достает поведенку по ключам из последнего снапшота Метрики"""
        try:
            res = await self.db.execute(
                select(MetrikaSnapshot)
                .where(MetrikaSnapshot.account_id == self.account_id)
                .order_by(MetrikaSnapshot.date.desc())
                .limit(1)
            )
            snap = res.scalar_one_or_none()
            if not snap or not snap.data: return {}
            
            # Формат в БД: {"keywords": [{"UTMTerm": "...", "visits": 5, "bounceRate": 40}, ...]}
            data = snap.data if isinstance(snap.data, dict) else json.loads(snap.data)
            kw_rows = data.get("keywords", [])
            
            mapping = {}
            for row in kw_rows:
                term = row.get("UTMTerm")
                if term:
                    mapping[term] = row
            return mapping
        except Exception as e:
            logger.warning(f"Failed to parse Metrika keywords: {e}")
            return {}

    async def _check_short_trend(self, lookback_start: date, lookback_end: date, days: int, baselines: dict) -> list[dict]:
        """S-009: Проверка резкого падения за последние N рабочих дней"""
        signals = []
        workdays = [lookback_start + timedelta(days=x) for x in range((lookback_end - lookback_start).days + 1) if _is_workday(lookback_start + timedelta(days=x))]
        recent_workdays = workdays[-days:] if len(workdays) >= days else workdays

        if not recent_workdays: return signals

        q = select(KeywordStat).where(and_(
            KeywordStat.account_id == self.account_id,
            KeywordStat.date.in_(recent_workdays)
        ))
        result = await self.db.execute(q)
        recent_stats = result.scalars().all()

        grouped = defaultdict(lambda: {"clicks": 0, "days": 0})
        for row in recent_stats:
            grouped[row.keyword_id]["clicks"] += int(row.clicks or 0)
            grouped[row.keyword_id]["days"] += 1

        for kw_id, data in grouped.items():
            if data["days"] < days: continue
            
            bl = baselines.get(kw_id, {})
            expected_total = bl.get("med_clicks", 0) * days
            
            if expected_total >= 2 and data["clicks"] == 0:
                kw_res = await self.db.execute(select(Keyword).where(Keyword.id == kw_id))
                kw = kw_res.scalar_one_or_none()
                if not kw: continue
                
                p = self._make_signal(f"S-009-{kw_id}", "short_trend_zero", "critical", "today", "bid_keyword",
                                      kw_id, kw.phrase, 0,
                                      f"0 кликов за последние {days} рабочих дней (ожидалось ~{expected_total:.0f}).",
                                      "Обвал ставки ЕПК ИЛИ кампания остановлена.",
                                      "Немедленно проверить статус кампании и ключа.", "Восстановление трафика")
                signals.append(p)
        
        return signals

    def _make_signal(self, signal_id, sig_type, severity, priority, layer, 
                     keyword_id, phrase, metric_value, description, hypothesis, 
                     action, expected_outcome, recommended_bid=None, clicks=0, spend=0,
                     entity_type=None, entity_id=None):
        """Фабрика для создания единообразного словаря сигнала"""
        return {
            "signal_id": signal_id,
            "type": sig_type,
            "severity": severity,
            "priority": priority,
            "layer": layer,
            "keyword_id": keyword_id,
            "phrase": phrase,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "metric_value": metric_value,
            "description": description,
            "hypothesis": hypothesis,
            "action": action,
            "expected_outcome": expected_outcome,
            "recommended_bid": recommended_bid,
            "clicks": clicks,
            "spend": round(spend, 2) if spend else 0,
        }
