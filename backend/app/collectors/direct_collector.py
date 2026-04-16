"""
Коллектор данных из Яндекс Директ API v5.
Собирает максимум данных для анализа уровня 1 (без CRM).
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
        self._client = httpx.AsyncClient(timeout=120.0)
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
                    raise DirectAPIError(
                        f"API error {data['error']['error_code']}: {data['error']['error_detail']}"
                    )
                return data.get("result", {})
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise
        raise DirectAPIError("Max retries exceeded")

    async def get_campaigns(self) -> list[dict]:
        """Кампании с полными настройками включая стратегию"""
        result = await self._post("campaigns", "get", {
            "SelectionCriteria": {},
            "FieldNames": [
                "Id", "Name", "Type", "Status", "State",
                "DailyBudget", "StartDate", "EndDate",
            ],
            "TextCampaignFieldNames": ["BiddingStrategy"],
            "DynamicTextCampaignFieldNames": ["BiddingStrategy"],
            "SmartCampaignFieldNames": ["BiddingStrategy"],
            "Page": {"Limit": 10000},
        })
        campaigns = result.get("Campaigns", [])
        # Нормализуем стратегию
        for c in campaigns:
            strategy = None
            for key in ["TextCampaign", "DynamicTextCampaign", "SmartCampaign"]:
                if key in c:
                    bs = c[key].get("BiddingStrategy", {})
                    # Определяем тип стратегии
                    if "ManualCpc" in str(bs) or "WbMaximumClicks" not in str(bs):
                        strategy = "MANUAL_CPC"
                    elif "AutotargetingCategories" in str(bs):
                        strategy = "AUTO"
                    else:
                        strategy = "AUTO"
                    break
            c["_strategy"] = strategy or "UNKNOWN"
        return campaigns

    async def get_ad_groups(self, campaign_ids: list[str]) -> list[dict]:
        result = await self._post("adgroups", "get", {
            "SelectionCriteria": {"CampaignIds": campaign_ids},
            "FieldNames": ["Id", "Name", "CampaignId", "Status", "ServingStatus"],
            "Page": {"Limit": 10000},
        })
        return result.get("AdGroups", [])

    async def get_keywords(self, campaign_ids: list[str]) -> list[dict]:
        result = await self._post("keywords", "get", {
            "SelectionCriteria": {"CampaignIds": campaign_ids},
            "FieldNames": [
                "Id", "Keyword", "AdGroupId", "CampaignId",
                "Bid", "ContextBid", "Status", "State",
                "StrategyPriority", "ServingStatus",
            ],
            "Page": {"Limit": 10000},
        })
        return result.get("Keywords", [])

    async def get_ads(self, campaign_ids: list[str]) -> list[dict]:
        """Объявления с ID для отслеживания изменений"""
        result = await self._post("ads", "get", {
            "SelectionCriteria": {"CampaignIds": campaign_ids},
            "FieldNames": [
                "Id", "AdGroupId", "CampaignId", "Status", "State",
                "Type", "ServingStatus",
            ],
            "TextAdFieldNames": ["Title", "Title2", "Text"],
            "Page": {"Limit": 10000},
        })
        return result.get("Ads", [])

    async def get_keyword_stats(
        self,
        date_from: date,
        date_to: date,
        campaign_ids: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        Полная статистика по ключевым словам.
        Включает все показатели для анализа уровня 1.
        """
        selection = {
            "DateFrom": date_from.isoformat(),
            "DateTo": date_to.isoformat(),
            "Filter": [
                {"Field": "Impressions", "Operator": "GREATER_THAN", "Values": ["0"]}
            ],
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
                    "Date",
                    "CampaignId",
                    "CampaignName",
                    "AdGroupId",
                    "AdGroupName",
                    "AdId",
                    "CriterionId",
                    "Criterion",
                    "CriterionType",
                    # Показатели
                    "Impressions",
                    "Clicks",
                    "Ctr",
                    "Cost",
                    "AvgCpc",
                    "AvgEffectiveBid",
                    "AvgTrafficVolume",
                    "AvgImpressionPosition",
                    "AvgClickPosition",
                    "WeightedCtr",
                    "WeightedImpressions",
                    "BounceRate",
                ],
                "ReportName": f"kw_stats_{date_from}_{date_to}",
                "ReportType": "CRITERIA_PERFORMANCE_REPORT",
                "DateRangeType": "CUSTOM_DATE",
                "Format": "TSV",
                "IncludeVAT": "NO",
                "IncludeDiscount": "NO",
            }
        }
        return await self._request_report(payload)

    async def get_search_queries(
        self,
        date_from: date,
        date_to: date,
        campaign_ids: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        Отчёт по поисковым запросам — реальные фразы по которым показывалась реклама.
        Ключевой источник для анализа нерелевантного трафика и расширения семантики.
        """
        selection = {
            "DateFrom": date_from.isoformat(),
            "DateTo": date_to.isoformat(),
            "Filter": [
                {"Field": "Clicks", "Operator": "GREATER_THAN", "Values": ["0"]}
            ],
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
                    "Date",
                    "CampaignId",
                    "CampaignName",
                    "AdGroupId",
                    "AdGroupName",
                    "CriterionId",
                    "Criterion",
                    "Query",
                    "Impressions",
                    "Clicks",
                    "Ctr",
                    "Cost",
                    "AvgCpc",
                    "AvgImpressionPosition",
                    "AvgClickPosition",
                    "MatchType",
                ],
                "ReportName": f"search_queries_{date_from}_{date_to}",
                "ReportType": "SEARCH_QUERY_PERFORMANCE_REPORT",
                "DateRangeType": "CUSTOM_DATE",
                "Format": "TSV",
                "IncludeVAT": "NO",
                "IncludeDiscount": "NO",
            }
        }
        return await self._request_report(payload)

    async def _request_report(self, payload: dict) -> list[dict]:
        """Запрос отчёта через Reports API с polling"""
        url = "https://api.direct.yandex.com/json/v5/reports"
        for attempt in range(10):
            resp = await self._client.post(
                url,
                json=payload,
                headers={
                    **self._headers(),
                    "processingMode": "offline",
                    "returnMoneyInMicros": "false",
                },
            )
            if resp.status_code == 200:
                return self._parse_tsv_report(resp.text)
            elif resp.status_code in (201, 202):
                wait = int(resp.headers.get("retryIn", 15))
                logger.info(f"Report not ready, waiting {wait}s...")
                await asyncio.sleep(wait)
            else:
                logger.error(f"Report error {resp.status_code}: {resp.text[:200]}")
                return []
        logger.error("Report not ready after max retries")
        return []

    def _parse_tsv_report(self, tsv: str) -> list[dict]:
        lines = tsv.strip().split("\n")
        if len(lines) < 3:
            return []
        headers = lines[1].split("\t")
        rows = []
        for line in lines[2:]:
            if line.startswith("Total") or line.startswith("Итого"):
                continue
            values = line.split("\t")
            if len(values) == len(headers):
                rows.append(dict(zip(headers, values)))
        return rows
