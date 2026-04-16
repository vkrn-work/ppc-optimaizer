"""
Коллектор данных из Яндекс Директ API v5.
Проектирован для мультикабинетности: account_id передаётся в каждый запрос.
"""
import asyncio
import logging
from datetime import date, timedelta
from typing import Any, Optional
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)


class DirectAPIError(Exception):
    pass


class YandexDirectCollector:
    API_URL = settings.YANDEX_DIRECT_API_URL

    def __init__(self, oauth_token: str, client_login: Optional[str] = None):
        self.oauth_token = oauth_token
        self.client_login = client_login
        self._client: Optional[httpx.AsyncClient] = None

    def _headers(self) -> dict:
        headers = {
            "Authorization": f"Bearer {self.oauth_token}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept-Language": "ru",
        }
        if self.client_login:
            headers["Client-Login"] = self.client_login
        return headers

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=60.0)
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    async def _post(self, service: str, method: str, params: dict) -> dict:
        url = f"{self.API_URL}/{service}"
        payload = {"method": method, "params": params}
        for attempt in range(3):
            try:
                resp = await self._client.post(url, json=payload, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
                if "error" in data:
                    raise DirectAPIError(f"API error {data['error']['error_code']}: {data['error']['error_detail']}")
                return data.get("result", {})
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise
        raise DirectAPIError("Max retries exceeded")

    async def get_campaigns(self) -> list[dict]:
        """Получить все кампании кабинета"""
        result = await self._post("campaigns", "get", {
            "SelectionCriteria": {},
            "FieldNames": ["Id", "Name", "Type", "Status", "DailyBudget", "StartDate"],
            "Page": {"Limit": 10000},
        })
        return result.get("Campaigns", [])

    async def get_ad_groups(self, campaign_ids: list[str]) -> list[dict]:
        """Получить группы объявлений"""
        result = await self._post("adgroups", "get", {
            "SelectionCriteria": {"CampaignIds": campaign_ids},
            "FieldNames": ["Id", "Name", "CampaignId", "Status"],
            "Page": {"Limit": 10000},
        })
        return result.get("AdGroups", [])

    async def get_keywords(self, campaign_ids: list[str]) -> list[dict]:
        """Получить все ключевые слова"""
        result = await self._post("keywords", "get", {
            "SelectionCriteria": {"CampaignIds": campaign_ids},
            "FieldNames": ["Id", "Keyword", "AdGroupId", "CampaignId", "Bid", "Status", "StrategyPriority"],
            "Page": {"Limit": 10000},
        })
        return result.get("Keywords", [])

    async def get_keyword_stats(
        self,
        date_from: date,
        date_to: date,
        campaign_ids: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        Получить статистику по ключевым словам за период.
        Возвращает строки отчёта из Reports API.
        """
        selection = {
            "DateFrom": date_from.isoformat(),
            "DateTo": date_to.isoformat(),
            "Filter": [{"Field": "Impressions", "Operator": "GREATER_THAN", "Values": ["0"]}],
        }
        if campaign_ids:
            selection["Filter"].append({
                "Field": "CampaignId",
                "Operator": "IN",
                "Values": campaign_ids,
            })

        payload = {
            "params": {
                "SelectionCriteria": selection,
                "FieldNames": [
                    "Date", "CampaignId", "AdGroupId", "CriterionId", "Criterion",
                    "Impressions", "Clicks", "Cost", "AvgCpc",
                    "AvgTrafficVolume", "AvgEffectiveBid",
                    "AvgImpressionPosition", "AvgClickPosition",
                ],
                "ReportName": f"keyword_stats_{date_from}_{date_to}",
                "ReportType": "CRITERIA_PERFORMANCE_REPORT",
                "DateRangeType": "CUSTOM_DATE",
                "Format": "TSV",
                "IncludeVAT": "NO",
                "IncludeDiscount": "NO",
            }
        }
        url = "https://api.direct.yandex.com/json/v5/reports"
        for attempt in range(5):
            resp = await self._client.post(
                url,
                json=payload,
                headers={**self._headers(), "processingMode": "offline", "returnMoneyInMicros": "false"},
            )
            if resp.status_code == 200:
                return self._parse_tsv_report(resp.text)
            elif resp.status_code == 201 or resp.status_code == 202:
                # Отчёт готовится
                wait = int(resp.headers.get("retryIn", 10))
                await asyncio.sleep(wait)
            else:
                resp.raise_for_status()
        raise DirectAPIError("Report not ready after retries")

    def _parse_tsv_report(self, tsv: str) -> list[dict]:
        lines = tsv.strip().split("\n")
        if len(lines) < 3:
            return []
        # Строка 0 — название отчёта, 1 — заголовки, последняя — "Total"
        headers = lines[1].split("\t")
        rows = []
        for line in lines[2:-1]:  # skip header and Total row
            values = line.split("\t")
            if len(values) == len(headers):
                rows.append(dict(zip(headers, values)))
        return rows

    async def get_search_queries(
        self,
        date_from: date,
        date_to: date,
        campaign_ids: Optional[list[str]] = None,
    ) -> list[dict]:
        """Получить отчёт по поисковым запросам"""
        selection = {
            "DateFrom": date_from.isoformat(),
            "DateTo": date_to.isoformat(),
            "Filter": [{"Field": "Clicks", "Operator": "GREATER_THAN", "Values": ["0"]}],
        }
        if campaign_ids:
            selection["Filter"].append({
                "Field": "CampaignId", "Operator": "IN", "Values": campaign_ids,
            })
        payload = {
            "params": {
                "SelectionCriteria": selection,
                "FieldNames": [
                    "Date", "CampaignId", "AdGroupId", "Criterion", "Query",
                    "Impressions", "Clicks", "Cost", "ConversionRate",
                    "MatchType",
                ],
                "ReportName": f"search_queries_{date_from}",
                "ReportType": "SEARCH_QUERY_PERFORMANCE_REPORT",
                "DateRangeType": "CUSTOM_DATE",
                "Format": "TSV",
                "IncludeVAT": "NO",
                "IncludeDiscount": "NO",
            }
        }
        url = "https://api.direct.yandex.com/json/v5/reports"
        for attempt in range(5):
            resp = await self._client.post(
                url,
                json=payload,
                headers={**self._headers(), "processingMode": "offline", "returnMoneyInMicros": "false"},
            )
            if resp.status_code == 200:
                return self._parse_tsv_report(resp.text)
            elif resp.status_code in (201, 202):
                wait = int(resp.headers.get("retryIn", 10))
                await asyncio.sleep(wait)
            else:
                resp.raise_for_status()
        return []
