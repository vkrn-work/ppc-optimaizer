"""
Запись в Яндекс Директ API v5 — применение одобренных suggestions в реальном
кабинете (app.core.tasks._apply_suggestion_async). Стиль и аутентификация намеренно
зеркально повторяют YandexDirectCollector из direct_collector.py.

ВАЖНО про единицы денег: API v5 принимает/возвращает денежные поля
(Bid, ContextBid и т.д.) в миллионных долях валюты — то же самое соглашение, по которому
в direct_collector.py AvgEffectiveBid делится на 1_000_000 при чтении. Здесь при
записи делаем обратное — умножаем рубли на 1_000_000.
"""
import logging
import asyncio
from typing import Optional
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)

MICROS = 1_000_000


class DirectWriteError(Exception):
    pass


class YandexDirectWriter:
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
            resp = await self._client.post(url, json=payload, headers=self._headers())
            if resp.status_code == 429:
                await asyncio.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise DirectWriteError(
                    f"API error {data['error'].get('error_code')}: "
                    f"{data['error'].get('error_string')} — {data['error'].get('error_detail')}"
                )
            return data.get("result", {})
        raise DirectWriteError("Max retries exceeded (429)")

    @staticmethod
    def _first_error(item: dict) -> Optional[str]:
        errors = item.get("Errors") or []
        if errors:
            e = errors[0]
            return f"{e.get('Code')}: {e.get('Message')} {e.get('Details','')}".strip()
        return None

    # ── Ставка ключа ─────────────────────────────────────────────────

    async def set_keyword_bid(self, keyword_direct_id: str, new_bid_rub: float) -> tuple[bool, str]:
        try:
            bid_micros = int(round(new_bid_rub * MICROS))
            result = await self._post("keywords", "update", {
                "Keywords": [{"Id": int(keyword_direct_id), "Bid": bid_micros}],
            })
            results = result.get("KeywordsUpdateResults", [])
            if not results:
                return False, "пустой ответ KeywordsUpdateResults"
            err = self._first_error(results[0])
            if err:
                return False, err
            return True, f"ставка установлена в {new_bid_rub:.0f}₽"
        except DirectWriteError as e:
            return False, str(e)
        except Exception as e:
            logger.exception("set_keyword_bid failed")
            return False, f"неожиданная ошибка: {e}"

    # ── Минус-слова на группу ───────────────────────────────────────────

    async def add_negative_keywords(self, ad_group_direct_id: str, negatives: list[str]) -> tuple[bool, str]:
        try:
            # NegativeKeywords в adgroups.update ПОЛНОСТЬЮ заменяет список у Яндекса,
            # поэтому сначала читаем текущий список и дописываем новые слова,
            # а не затираем существующие.
            get_result = await self._post("adgroups", "get", {
                "SelectionCriteria": {"Ids": [int(ad_group_direct_id)]},
                "FieldNames": ["Id", "NegativeKeywords"],
            })
            groups = get_result.get("AdGroups", [])
            existing = []
            if groups:
                existing = (groups[0].get("NegativeKeywords") or {}).get("Items", [])
            merged = list(dict.fromkeys([*existing, *negatives]))

            result = await self._post("adgroups", "update", {
                "AdGroups": [{
                    "Id": int(ad_group_direct_id),
                    "NegativeKeywords": {"Items": merged},
                }],
            })
            results = result.get("AdGroupsUpdateResults", [])
            if not results:
                return False, "пустой ответ AdGroupsUpdateResults"
            err = self._first_error(results[0])
            if err:
                return False, err
            return True, f"добавлено {len(negatives)} минус-слов (всего в группе: {len(merged)})"
        except DirectWriteError as e:
            return False, str(e)
        except Exception as e:
            logger.exception("add_negative_keywords failed")
            return False, f"неожиданная ошибка: {e}"

    # ── Остановка ключа ───────────────────────────────────────────────────

    async def suspend_keyword(self, keyword_direct_id: str) -> tuple[bool, str]:
        try:
            result = await self._post("keywords", "suspend", {
                "SelectionCriteria": {"Ids": [int(keyword_direct_id)]},
            })
            results = result.get("SuspendResults", [])
            if not results:
                return False, "пустой ответ SuspendResults"
            err = self._first_error(results[0])
            if err:
                return False, err
            return True, "ключ остановлен"
        except DirectWriteError as e:
            return False, str(e)
        except Exception as e:
            logger.exception("suspend_keyword failed")
            return False, f"неожиданная ошибка: {e}"
