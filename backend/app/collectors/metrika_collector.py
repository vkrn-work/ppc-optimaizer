"""
Коллектор данных из Яндекс Метрики.
Собирает максимум данных для анализа качества трафика из Директа.
"""
import logging
from datetime import date
from typing import Optional
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)


class MetrikaCollector:
    API_URL = settings.YANDEX_METRIKA_API_URL

    def __init__(self, oauth_token: str, counter_id: str):
        self.oauth_token = oauth_token
        self.counter_id = counter_id
        self._client: Optional[httpx.AsyncClient] = None

    def _headers(self) -> dict:
        return {"Authorization": f"OAuth {self.oauth_token}"}

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=60.0)
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    async def _get(self, params: dict) -> list[dict]:
        """Базовый запрос к Stat API"""
        resp = await self._client.get(
            f"{self.API_URL}/stat/v1/data",
            params=params,
            headers=self._headers(),
        )
        resp.raise_for_status()
        return self._parse(resp.json())

    def _parse(self, data: dict) -> list[dict]:
        dimensions = data.get("query", {}).get("dimensions", [])
        metrics_names = data.get("query", {}).get("metrics", [])
        rows = []
        for row in data.get("data", []):
            record = {}
            for i, dim_info in enumerate(row.get("dimensions", [])):
                key = dimensions[i].replace("ym:s:", "") if i < len(dimensions) else f"dim_{i}"
                record[key] = dim_info.get("name") or dim_info.get("id")
            for i, val in enumerate(row.get("metrics", [])):
                key = metrics_names[i].replace("ym:s:", "") if i < len(metrics_names) else f"m_{i}"
                record[key] = round(val, 2) if isinstance(val, float) else val
            rows.append(record)
        return rows

    def _base_params(self, date_from: date, date_to: date, extra_filter: str = "") -> dict:
        params = {
            "id": self.counter_id,
            "date1": date_from.strftime("%Y-%m-%d"),
            "date2": date_to.strftime("%Y-%m-%d"),
            "limit": 10000,
        }
        direct_filter = "ym:s:UTMSource=='direct'"
        params["filters"] = f"{direct_filter} AND {extra_filter}" if extra_filter else direct_filter
        return params

    # ── 1. Сводка по трафику ─────────────────────────────────────────────────

    async def get_traffic_summary(self, date_from: date, date_to: date) -> dict:
        """Общие KPI трафика из Директа"""
        params = self._base_params(date_from, date_to)
        params.update({
            "metrics": ",".join([
                "ym:s:visits",
                "ym:s:users",
                "ym:s:newUsers",
                "ym:s:bounceRate",
                "ym:s:pageDepth",
                "ym:s:avgVisitDurationSeconds",
            ]),
            "dimensions": "ym:s:UTMSource",
        })
        rows = await self._get(params)
        return rows[0] if rows else {}

    # ── 2. По кампаниям ──────────────────────────────────────────────────────

    async def get_campaigns_stats(self, date_from: date, date_to: date) -> list[dict]:
        """Метрики по каждой рекламной кампании"""
        params = self._base_params(date_from, date_to)
        params.update({
            "metrics": ",".join([
                "ym:s:visits",
                "ym:s:bounceRate",
                "ym:s:pageDepth",
                "ym:s:avgVisitDurationSeconds"
            ]),
            "dimensions": "ym:s:UTMCampaign",
            "sort": "-ym:s:visits",
        })
        return await self._get(params)

    # ── 3. По ключевым словам ────────────────────────────────────────────────

    async def get_keyword_stats(self, date_from: date, date_to: date) -> list[dict]:
        """Метрики по ключевым словам (utm_term) — для матчинга с Директом"""
        params = self._base_params(date_from, date_to)
        params.update({
            "metrics": ",".join([
                "ym:s:visits",
                "ym:s:bounceRate",
                "ym:s:pageDepth",
                "ym:s:avgVisitDurationSeconds"
            ]),
            "dimensions": "ym:s:UTMTerm,ym:s:UTMCampaign",
            "sort": "-ym:s:visits",
        })
        return await self._get(params)

    # ── 4. По устройствам ────────────────────────────────────────────────────

    async def get_device_stats(self, date_from: date, date_to: date) -> list[dict]:
        """Разбивка по типу устройства — важно для B2B"""
        params = self._base_params(date_from, date_to)
        params.update({
            "metrics": ",".join([
                "ym:s:visits",
                "ym:s:bounceRate",
                "ym:s:avgVisitDurationSeconds",
            ]),
            "dimensions": "ym:s:deviceCategory",
            "sort": "-ym:s:visits",
        })
        return await self._get(params)

    # ── 5. По регионам ───────────────────────────────────────────────────────

    async def get_region_stats(self, date_from: date, date_to: date) -> list[dict]:
        """Разбивка по регионам — для корректировок ставок по гео"""
        params = self._base_params(date_from, date_to)
        params.update({
            "metrics": ",".join([
                "ym:s:visits",
                "ym:s:bounceRate",
                "ym:s:avgVisitDurationSeconds",
            ]),
            "dimensions": "ym:s:regionCity",
            "sort": "-ym:s:visits",
            "limit": 50,
        })
        return await self._get(params)

    # ── 6. По посадочным страницам ───────────────────────────────────────────

    async def get_landing_stats(self, date_from: date, date_to: date) -> list[dict]:
        """Какие страницы получают трафик из Директа и как конвертируют"""
        params = self._base_params(date_from, date_to)
        params.update({
            "metrics": ",".join([
                "ym:s:visits",
                "ym:s:bounceRate",
                "ym:s:pageDepth",
                "ym:s:avgVisitDurationSeconds"
            ]),
            "dimensions": "ym:s:startURL",
            "sort": "-ym:s:visits",
            "limit": 30,
        })
        return await self._get(params)

    # ── 7. По времени суток и дням ───────────────────────────────────────────

    async def get_time_stats(self, date_from: date, date_to: date) -> list[dict]:
        """По дням — динамика трафика"""
        params = self._base_params(date_from, date_to)
        params.update({
            "metrics": ",".join([
                "ym:s:visits",
                "ym:s:bounceRate",
            ]),
            "dimensions": "ym:s:date",
            "sort": "ym:s:date",
        })
        return await self._get(params)

    async def get_hour_stats(self, date_from: date, date_to: date) -> list[dict]:
        """По часам — для корректировок ставок по расписанию"""
        params = self._base_params(date_from, date_to)
        params.update({
            "metrics": ",".join([
                "ym:s:visits",
                "ym:s:bounceRate",
            ]),
            "dimensions": "ym:s:hourOfDay",
            "sort": "ym:s:hourOfDay",
        })
        return await self._get(params)

    async def get_weekday_stats(self, date_from: date, date_to: date) -> list[dict]:
        """По дням недели"""
        params = self._base_params(date_from, date_to)
        params.update({
            "metrics": ",".join([
                "ym:s:visits",
                "ym:s:bounceRate",
            ]),
            "dimensions": "ym:s:dayOfWeek",
            "sort": "ym:s:dayOfWeek",
        })
        return await self._get(params)

    # ── 8. По браузерам и ОС ─────────────────────────────────────────────────

    async def get_browser_stats(self, date_from: date, date_to: date) -> list[dict]:
        """Браузеры и ОС — для диагностики технических проблем"""
        params = self._base_params(date_from, date_to)
        params.update({
            "metrics": "ym:s:visits,ym:s:bounceRate",
            "dimensions": "ym:s:browser,ym:s:operatingSystem",
            "sort": "-ym:s:visits",
            "limit": 20,
        })
        return await self._get(params)

    # ── 9. Новые vs вернувшиеся ──────────────────────────────────────────────

    async def get_new_vs_return(self, date_from: date, date_to: date) -> list[dict]:
        """Новые vs вернувшиеся пользователи"""
        params = self._base_params(date_from, date_to)
        params.update({
            "metrics": ",".join([
                "ym:s:visits",
                "ym:s:bounceRate",
                "ym:s:avgVisitDurationSeconds",
            ]),
            "dimensions": "ym:s:userType",
        })
        return await self._get(params)

    # ── 10. Цели детально ───────────────────────────────────────────────────

    async def get_goal_completions(
        self,
        date_from: date,
        date_to: date,
        goal_id: Optional[str] = None,
    ) -> list[dict]:
        """Достижения целей с атрибуцией по кампаниям и ключам"""
        metrics = f"ym:s:goal{goal_id}reaches" if goal_id else "ym:s:visits"
        params = self._base_params(date_from, date_to)
        params.update({
            "metrics": metrics,
            "dimensions": "ym:s:UTMCampaign,ym:s:UTMTerm",
            "sort": f"-{metrics}",
            "limit": 1000,
        })
        return await self._get(params)

    # ── 11. Полный сбор для хранилища ────────────────────────────────────────

    async def collect_all(self, date_from: date, date_to: date) -> dict:
        """
        Собрать все данные за один вызов.
        Возвращает словарь с ключами для записи в БД.
        """
        logger.info(f"Collecting Metrika data for counter {self.counter_id}: {date_from} — {date_to}")
        result = {}
        collectors = {
            "summary":      self.get_traffic_summary,
            "campaigns":    self.get_campaigns_stats,
            "keywords":     self.get_keyword_stats,
            "devices":      self.get_device_stats,
            "regions":      self.get_region_stats,
            "landings":     self.get_landing_stats,
            "by_day":       self.get_time_stats,
            "by_hour":      self.get_hour_stats,
            "by_weekday":   self.get_weekday_stats,
            "browsers":     self.get_browser_stats,
            "user_type":    self.get_new_vs_return,
            "goals":        self.get_goal_completions,
        }
        for key, method in collectors.items():
            try:
                result[key] = await method(date_from, date_to)
                logger.info(f"Metrika {key}: {len(result[key]) if isinstance(result[key], list) else 'ok'}")
            except Exception as e:
                logger.warning(f"Metrika {key} failed: {e}")
                result[key] = []
        return result
