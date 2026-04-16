"""
Коллектор данных из Яндекс Метрики.
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
        return {
            "Authorization": f"OAuth {self.oauth_token}",
        }

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=60.0)
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    async def get_visits_by_utm(
        self,
        date_from: date,
        date_to: date,
    ) -> list[dict]:
        """
        Получить визиты с разбивкой по UTM-меткам и ClientID.
        Нужно для матчинга клик → заявка.
        """
        params = {
            "id": self.counter_id,
            "date1": date_from.strftime("%Y-%m-%d"),
            "date2": date_to.strftime("%Y-%m-%d"),
            "metrics": "ym:s:visits,ym:s:pageviews,ym:s:bounceRate,ym:s:avgVisitDurationSeconds",
            "dimensions": (
                "ym:s:UTMSource,ym:s:UTMMedium,ym:s:UTMCampaign,"
                "ym:s:UTMTerm,ym:s:clientID,ym:s:date"
            ),
            "filters": "ym:s:UTMSource=='yandex'",
            "limit": 10000,
            "sort": "-ym:s:visits",
        }
        resp = await self._client.get(
            f"{self.API_URL}/stat/v1/data",
            params=params,
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        return self._parse_stat_response(data)

    async def get_goal_completions(
        self,
        date_from: date,
        date_to: date,
        goal_id: Optional[str] = None,
    ) -> list[dict]:
        """Получить достижения целей с атрибуцией"""
        metrics = "ym:s:goalReachesAny"
        if goal_id:
            metrics = f"ym:s:goal{goal_id}reaches"

        params = {
            "id": self.counter_id,
            "date1": date_from.strftime("%Y-%m-%d"),
            "date2": date_to.strftime("%Y-%m-%d"),
            "metrics": metrics,
            "dimensions": (
                "ym:s:UTMSource,ym:s:UTMCampaign,ym:s:UTMTerm,"
                "ym:s:clientID,ym:s:date"
            ),
            "filters": "ym:s:UTMSource=='yandex'",
            "limit": 10000,
        }
        resp = await self._client.get(
            f"{self.API_URL}/stat/v1/data",
            params=params,
            headers=self._headers(),
        )
        resp.raise_for_status()
        return self._parse_stat_response(resp.json())

    async def get_traffic_summary(
        self,
        date_from: date,
        date_to: date,
    ) -> dict:
        """Сводка по трафику из Директа"""
        params = {
            "id": self.counter_id,
            "date1": date_from.strftime("%Y-%m-%d"),
            "date2": date_to.strftime("%Y-%m-%d"),
            "metrics": "ym:s:visits,ym:s:users,ym:s:bounceRate,ym:s:avgVisitDurationSeconds,ym:s:pageDepth",
            "dimensions": "ym:s:UTMSource",
            "filters": "ym:s:UTMSource=='yandex'",
        }
        resp = await self._client.get(
            f"{self.API_URL}/stat/v1/data",
            params=params,
            headers=self._headers(),
        )
        resp.raise_for_status()
        rows = self._parse_stat_response(resp.json())
        return rows[0] if rows else {}

    def _parse_stat_response(self, data: dict) -> list[dict]:
        dimensions = data.get("query", {}).get("dimensions", [])
        metrics_names = data.get("query", {}).get("metrics", [])
        rows = []
        for row in data.get("data", []):
            record = {}
            for i, dim_info in enumerate(row.get("dimensions", [])):
                key = dimensions[i].replace("ym:s:", "") if i < len(dimensions) else f"dim_{i}"
                record[key] = dim_info.get("name") or dim_info.get("id")
            for i, val in enumerate(row.get("metrics", [])):
                key = metrics_names[i].replace("ym:s:", "") if i < len(metrics_names) else f"metric_{i}"
                record[key] = val
            rows.append(record)
        return rows
