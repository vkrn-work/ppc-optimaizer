"""
CR-анализатор v4.1 — Топологический движок (Senior PPC).
Исправления: Баг выходных дней, контекст цены, логика TrafficVolume, пороги Метрики, пустые массивы.
Все SQL запросы однострочные для безопасного копирования.
"""
import json
import logging
import statistics
from datetime import datetime, timedelta, date
from collections import defaultdict
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import Keyword, KeywordStat, Campaign, AdGroup, AnalysisResult, Account, MetrikaSnapshot

logger = logging.getLogger(__name__)

CONFIG = {
    "delta_vol": 0.30, "delta_rat": 0.20, "min_age_days": 28, "min_bl_clicks": 2.0, "min_bl_impr": 10,
    "crm_enabled": False, "mql_sql_window": 28, "min_clicks_crm": 30, "min_visits_metrika": 15,
}

def _is_workday(d: date) -> bool:
    return d.weekday() < 5

def safe_median(lst):
    filtered = [x for x in lst if x > 0]
    return statistics.median(filtered) if filtered else 0

class CRAnalyzer:
    def __init__(self, db: AsyncSession, account_id: int):
        self.db = db
        self.account_id = account_id

    async def run_full_analysis(self, period_days: int = 28) -> AnalysisResult:
        period_end = datetime.utcnow().date()
        period_start = period_end - timedelta(days=period_days)
        baseline_start = period_start - timedelta(days=period_days)
        analysis = AnalysisResult(account_id=self.account_id, period_start=datetime.combine(period_start, datetime.min.time()), period_end=datetime.combine(period_end, datetime.min.time()))
        self.db.add(analysis)
        await self.db.flush()
        acc_res = await self.db.execute(select(Account).where(Account.id == self.account_id))
        account = acc_res.scalar_one_or_none()
        cfg = {**CONFIG}
        if account and account.analysis_config:
            try: cfg.update(json.loads(account.analysis_config))
            except: pass
        target_cpl = float(getattr(account, 'target_cpl', None) or cfg.get("target_cpl", 2000))
        
        raw_stats = await self._get_raw_stats(baseline_start, period_end)
        kw_bl, kw_curr = self._calc_baselines(raw_stats, baseline_start, period_start, period_end)
        metrika_kw = await self._get_metrika_data()
        
        # ИСПРАВЛЕНАЯ ЗАГРУЗКА КАМПАНИЙ (используем правильные имена полей из models.py)
        camp_res = await self.db.execute(
            select(AdGroup.id, AdGroup.campaign_id, Campaign.strategy_type).join(Campaign, AdGroup.campaign_id == Campaign.id)
        )
        camp_cache = {}
        for grp_id, camp_id, strat in camp_res.all():
            camp_cache[grp_id] = camp_id
            camp_cache[f"{grp_id}_m"] = (strat != "AUTO")

        problems = []
        for kw_id, curr in kw_curr.items():
            bl = kw_bl.get(kw_id)
            if not bl: continue
            kw_res = await self.db.execute(select(Keyword).where(Keyword.id == kw_id))
            kw = kw_res.scalar_one_or_none()
            if not kw: continue
            
            grp_id = kw.ad_group_id
            phrase = kw.phrase
            is_manual = camp_cache.get(f"{grp_id}_m", False)
            tags = set()
            
            c_c, c_i, c_s = curr["clicks"], curr["impressions"], curr["spend"]
            b_c, b_i, b_s = bl["clicks"], bl["impressions"], bl["spend"]
            c_pos, c_tv = curr["avg_position"], curr["traffic_volume"]
            c_bid = float(kw.current_bid or 0)
            
            c_cpc = c_s / c_c if c_c > 0 else 0
            b_cpc = b_s / b_c if b_c > 0 else 0

            if c_c < b_c * (1 - cfg["delta_vol"]) and b_c >= cfg["min_bl_clicks"]:
                impr_drop = (c_i - b_i) / b_i if b_i > 0 else 0
                if impr_drop < -cfg["delta_vol"]:
                    if c_pos > 3.5 and is_manual:
                        if c_cpc < target_cpl * 1.2:
                            rec_bid = round(c_bid * 1.3, 2) if c_bid > 0 else 0
                            p = self._diag(kw_id, phrase, grp_id, "5A", "critical", f"Клики -{abs((c_c-b_c)/b_c*100):.0f}%. Показы -{abs(impr_drop)*100:.0f}%. Позиция {c_pos:.1f}.", "Ставка не держит аукцион.", f"Поднять ставку до {rec_bid:.0f} руб.", rec_bid)
                            problems.append(p); tags.add("BID")
                    elif c_pos <= 3.5:
                        bl_tv = bl.get("traffic_volume", 0)
                        if c_tv < 30 and bl_tv > 40:
                            p = self._diag(kw_id, phrase, grp_id, "5B", "warning", f"Клики/Показы -{abs(impr_drop)*100:.0f}%. Позиция {c_pos:.1f}, НО TraffVol упал с {bl_tv:.0f} до {c_tv}.", "Конкуренты перебили ставки.", "Поднять ставку на 15-20%.")
                            problems.append(p); tags.add("BID")
                        else:
                            p = self._diag(kw_id, phrase, grp_id, "5B", "info", f"Клики/Показы -{abs(impr_drop)*100:.0f}%. Позиция {c_pos:.1f}, TraffVol стабильный.", "Сезонный спад спроса.", "Ставки не трогать.")
                            problems.append(p); tags.add("SEASON")
            elif c_i >= b_i * 0.8 and b_i >= cfg["min_bl_impr"]:
                if c_c < b_c * (1 - cfg["delta_vol"]):
                    ctr_c = (c_c/c_i*100) if c_i>0 else 0
                    ctr_b = (b_c/b_i*100) if b_i>0 else 0
                    pos_gap = curr["avg_click_position"] - c_pos if curr["avg_click_position"] > 0 else 0
                    wctr_drop = (curr.get("weighted_ctr", 0) or 0) < (bl.get("weighted_ctr", 0) or 0) * 0.8
                    if pos_gap > 1.5 or wctr_drop:
                        p = self._diag(kw_id, phrase, grp_id, "4A", "warning", f"CTR упал ({ctr_b:.1f}% -> {ctr_c:.1f}%). Разрыв поз. показа/клика {pos_gap:.1f}.", "Объявление проигрывает конкурентам.", "Переписать заголовок под марку.")
                        problems.append(p); tags.add("CREATIVE")
                    else:
                        p = self._diag(kw_id, phrase, grp_id, "4B", "warning", f"CTR упал ({ctr_b:.1f}% -> {ctr_c:.1f}%). Поз./TraffVol стабильны.", "Выгорание креатива.", "A/B тест заголовка.")
                        problems.append(p); tags.add("CREATIVE")
            
            if "CREATIVE" not in tags and "BID" not in tags:
                m_data = metrika_kw.get(phrase, {})
                m_br = float(m_data.get("bounceRate") or 0)
                m_visits = int(m_data.get("visits") or 0)
                if m_visits >= cfg["min_visits_metrika"] and m_br > 70:
                    sev = "critical" if m_br > 80 else "warning"
                    p = self._diag(kw_id, phrase, grp_id, "8A", sev, f"Bounce Rate {m_br:.0f}% (Visits: {m_visits}).", "Мусорный трафик ИЛИ нецелевой лендинг.", "Выгрузить поисковые запросы. Минус-слова.")
                    problems.append(p); tags.add("QUALITY")
                if m_visits >= 5:
                    gap_cv = (c_c - m_visits) / c_c if c_c > 5 else 0
                    if gap_cv > 0.20:
                        p = self._diag(kw_id, phrase, grp_id, "1E", "critical", f"Кликов: {c_c}, Визитов: {m_visits}. Разрыв {gap_cv*100:.0f}%.", "Сайт недоступен.", "ПАУЗА СТАВОК. Проверить сайт!")
                        problems.append(p); tags.add("SITE_DOWN")

            if "BID" not in tags and c_pos <= 1.5 and c_tv > 100:
                if b_cpc > 0 and c_cpc > b_cpc * 1.3:
                    p = self._diag(kw_id, phrase, grp_id, "9A", "warning", f"Поз. {c_pos:.1f}, TraffVol {c_tv}. CPC: {b_cpc:.0f} -> {c_cpc:.0f} руб.", "Потолок трафика. Переплата.", "Снизить ставку на 15-20%.")
                    problems.append(p); tags.add("OVERPAY")

            if not tags and 2.5 < c_pos < 4.0 and c_tv > 0:
                m_data_p = metrika_kw.get(phrase, {})
                m_br_p = float(m_data_p.get("bounceRate") or 100)
                if m_br_p < 50:
                    p = self._diag(kw_id, phrase, grp_id, "P1", "info", f"Отл. поведение (BR {m_br_p:.0f}%), Поз. {c_pos:.1f}, TraffVol {c_tv}.", "Недоинвестированный конверсионник.", "Поднять ставку на 30%.")
                    problems.append(p)

        sev_ord = {"critical": 0, "warning": 1, "info": 2}
        problems = sorted(problems, key=lambda x: (sev_ord.get(x["severity"], 99), x["layer"] != "keyword"))[:30]
        analysis.problems = problems
        analysis.summary = {"keywords_analyzed": len(kw_curr), "problems_found": len(problems), "signals_by_severity": {s: sum(1 for p in problems if p["severity"]==s) for s in ["critical","warning","info"]}, "has_crm_data": cfg["crm_enabled"]}
        await self.db.commit()
        logger.info(f"Analysis v4.1 Top-Down: {len(problems)} diagnosed issues.")
        return analysis

    def _diag(self, kw_id, phrase, grp_id, branch, sev, desc, hypo, action, rec_bid=None):
        return {"signal_id": f"{branch}-{kw_id}", "type": branch, "severity": sev, "layer": "keyword", "keyword_id": kw_id, "phrase": phrase, "group_id": grp_id, "description": desc, "hypothesis": hypo, "action": action, "recommended_bid": rec_bid}

    async def _get_raw_stats(self, start, end):
        res = await self.db.execute(select(KeywordStat).where(and_(KeywordStat.account_id == self.account_id, KeywordStat.date >= start, KeywordStat.date <= end)))
        return res.scalars().all()

    def _calc_baselines(self, stats, bl_start, bl_end, curr_end):
        kw_bl_data, kw_curr_data = defaultdict(list), defaultdict(list)
        for r in stats:
            d = r.date.date()
            is_bl = _is_workday(d) and bl_start <= d < bl_end
            is_curr = _is_workday(d) and bl_end <= d <= curr_end
            c, i, s = int(r.clicks or 0), int(r.impressions or 0), float(r.spend or 0)
            p, cp, ct, b, w, tv = float(r.avg_position or 0), float(r.avg_click_position or 0), float(r. rctr or 0), float(r.avg_bid or 0), float(r.weighted_ctr or 0), int(r.traffic_volume or 0)
            if is_bl and (c > 0 or i > 0):
                kw_bl_data[r.keyword_id].append({"c": c, "i": i, "s": s, "p": p, "cp": cp, "ct": ct, "b": b, "w": w, "tv": tv})
            if is_curr:
                kw_curr_data[r.keyword_id].append({"c": c, "i": i, "s": s, "p": p, "cp": cp, "ct": ct, "b": b, "w": w, "tv": tv})

        def agg(data_list):
            if not data_list: return None
            n = len(data_list)
            return {"clicks": safe_median([d["c"] for d in data_list]), "impressions": safe_median([d["i"] for d in data_list]), "spend": sum(d["s"] for d in data_list), "avg_position": sum(d["p"] for d in data_list)/n if n else 0, "avg_click_position": sum(d["cp"] for d in data_list)/n if n else 0, "ctr": sum(d["ct"] for d in data_list)/n if n else 0, "avg_bid": sum(d["b"] for d in data_list)/n if n else 0, "weighted_ctr": sum(d["w"] for d in data_list)/n if n else 0, "traffic_volume": sum(d["tv"] for d in data_list)/n if n else 0}

        final_bl, final_curr = {}, {}
        for kid, data in kw_bl_data.items():
            if sum(d["i"] for d in data) < 10: continue
            final_bl[kid] = agg(data)
        for kid, data in kw_curr_data.items():
            final_curr[kid] = agg(data)
        return final_bl, final_curr

    async def _get_metrika_data(self) -> dict:
        try:
            res = await self.db.execute(select(MetrikaSnapshot).where(MetrikaSnapshot.account_id == self.account_id).order_by(MetrikaSnapshot.date.desc()).limit(1))
            snap = res.scalar_one_or_none()
            if not snap or not snap.data: return {}
            data = snap.data if isinstance(snap.data, dict) else json.loads(snap.data)
            return {row.get("UTMTerm"): row for row in data.get("keywords", []) if row.get("UTMTerm")}
        except: return {}
