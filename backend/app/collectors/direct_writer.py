"""
Запись в Яндекс Директ API v5 — применение одобренных suggestions в реальном
кабинете (app.core.tasks._apply_suggestion_async). Стиль и аутентификация намеренно зеркально повторяют
YandexDirectCollector из direct_collector.py.

ВАЖНО про единицы денег: API v5 принимает/возвращает денежные поля
(Bid, ContextBid и т.д.) в миллионных долях валюты — то же самое соглашение, по которому
в direct_collector.py AvgEffectiveBid делится на 1_000_000 при чтении. Здесь при
записи делаем обратное — умножаем рубли на 1_000_000.

v1.5.1: ключи ответа update/suspend-методов именуются по МЕТОДУ ("UpdateResults"
для ЛЮБОГО .update), а не по сервису.

v1.5.2: negative_keywords от LLM могут прийти с приклеенным "-" (модель
иногда копирует формат из фраз ключей в датасете, где минус-слова уже с "-")
— Direct API отклоняет такие слова ошибкой 5002 (дефис в начале/конце слова
недопустим). Чистим каждое слово от ведущих/хвостовых "-"/"+" перед отправкой —
не полагаемся только на формат ответа модели.

v1.6.0: добавлен add_keywords() — добавление НОВЫХ ключевых слов в существующую
группу через keywords.add (используется страницей «Задачи ИИ» — пользователь
свободным текстом описывает товар, ИИ генерит фразы, после одобрения они
пишутся в кабинет этим методом). Ответ .add-методов, по той же логике что и
.update, именуется по методу — "AddResults".
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


def _clean_negative_word(word: str) -> str:
    """Снимает ведущие/хвостовые "-"/"+" и пробелы — Direct API принимает
    в NegativeKeywords голые слова/фразы, знак минуса добавляет сампри применении."""
    return " ".join(
        tok.strip("-+") for tok in word.strip().split()
    ).strip()


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

    # ── Ставка ключа ─────────────────────────────────────────

    async def set_keyword_bid(self, keyword_direct_id: str, new_bid_rub: float) -> tuple[bool, str]:
        try:
            bid_micros = int(round(new_bid_rub * MICROS))
            result = await self._post("keywords", "update", {
                "Keywords": [{"Id": int(keyword_direct_id), "Bid": bid_micros}],
            })
            results = result.get("UpdateResults", [])
            if not results:
                return False, "пустой ответ UpdateResults"
            err = self._first_error(results[0])
            if err:
                return False, err
            return True, f"ставка установлена в {new_bid_rub:.0f}₽"
        except DirectWriteError as e:
            return False, str(e)
        except Exception as e:
            logger.exception("set_keyword_bid failed")
            return False, f"неожиданная ошибка: {e}"

    # ── Минус-слова на группу ──────────────────────────────────

    async def add_negative_keywords(self, ad_group_direct_id: str, negatives: list[str]) -> tuple[bool, str]:
        try:
            negatives = [w for w in (_clean_negative_word(n) for n in negatives) if w]
            if not negatives:
                return False, "пустой список минус-слов после очистки"

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
            results = result.get("UpdateResults", [])
            if not results:
                return False, "пустой ответ UpdateResults"
            err = self._first_error(results[0])
            if err:
                return False, err
            return True, f"добавлено {len(negatives)} минус-слов (всего в группе: {len(merged)})"
        except DirectWriteError as e:
            return False, str(e)
        except Exception as e:
            logger.exception("add_negative_keywords failed")
            return False, f"неожиданная ошибка: {e}"

    # ── Новые ключевые слова в группу ──────────────────────────

    async def add_keywords(self, ad_group_direct_id: str, keywords: list[str]) -> tuple[bool, str]:
        """Добавляет НОВЫЕ ключевые фразы в существующую группу объявлений через
        keywords.add. Не трогает ставку (Bid не указывается — группа/кампания
        назначит её по своей стратегии, как для любого нового ключа вручную
        через интерфейс). Источник: страница «Задачи ИИ» после одобрения."""
        try:
            phrases = [w.strip() for w in keywords if w and w.strip()]
            if not phrases:
                return False, "пустой список ключевых слов"

            items = [{"AdGroupId": int(ad_group_direct_id), "Keyword": phrase} for phrase in phrases]
            result = await self._post("keywords", "add", {"Keywords": items})
            results = result.get("AddResults", [])
            if not results:
                return False, "пустой ответ AddResults"

            errors = [self._first_error(r) for r in results]
            ok_count = sum(1 for e in errors if not e)
            if ok_count == 0:
                return False, "; ".join(e for e in errors if e) or "все ключи отклонены Директом"
            if ok_count < len(results):
                return True, (
                    f"добавлено {ok_count} из {len(results)} ключей "
                    f"(отклонены: {'; '.join(e for e in errors if e)})"
                )
            return True, f"добавлено {ok_count} новых ключевых слов"
        except DirectWriteError as e:
            return False, str(e)
        except Exception as e:
            logger.exception("add_keywords failed")
            return False, f"неожиданная ошибка: {e}"

    # ── Создание новой кампании целиком (v1.7.0, пункт 6 запроса) ─────
    #
    # ВАЖНО — этот блок кода НИ РАЗУ не выполнялся против реального Yandex
    # Direct API (в отличие от set_keyword_bid/add_negative_keywords/
    # add_keywords/suspend_keyword — те уже подтверждены на живом кабинете,
    # см. ppc_optimizer_status_v1.6.0.md). Структура запроса собрана по той же
    # логике, что и уже проверенные методы этого файла (единицы измерения,
    # формат payload service/method/params), но требует ОДНОГО контролируемого
    # тестового прогона на дешёвой кампании перед тем, как на неё можно
    # полагаться — тот же путь, который прошли add_keywords и agent_command
    # перед тем как их можно было считать проверенными.
    #
    # Осознанно НЕ создаём кампанию сразу активной с боевым бюджетом:
    # используется ManualCpc (TEXT_CAMPAIGN) — предсказуемая ручная стратегия,
    # рекомендованная в yandex_direct_knowledge.py для новых кампаний с редкими
    # конверсиями, вместо автостратегии/ЕПК, которая "наказывает" свежие ключи
    # без быстрой статистики (см. кейс "Листовой прокат" в PPC_Audit_Playbook).

    async def create_campaign(self, name: str, daily_budget_rub: float) -> tuple[bool, str]:
        """Создаёт TEXT_CAMPAIGN (ТГК) на ручной стратегии ManualCpc с дневным
        бюджетом. Возвращает (ok, campaign_direct_id_как_строка ИЛИ текст ошибки)."""
        try:
            from datetime import date, timedelta
            start_date = (date.today() + timedelta(days=1)).isoformat()
            budget_micros = int(round(daily_budget_rub * MICROS))
            result = await self._post("campaigns", "add", {
                "Campaigns": [{
                    "Name": name[:250],
                    "StartDate": start_date,
                    "DailyBudget": {
                        "Amount": budget_micros,
                        "Mode": "STANDARD",
                    },
                    "TextCampaign": {
                        "BiddingStrategy": {
                            "Search": {
                                "BiddingStrategyType": "HIGHEST_POSITION",
                            },
                            "Network": {
                                "BiddingStrategyType": "SERVING_OFF",
                            },
                        },
                    },
                }],
            })
            results = result.get("AddResults", [])
            if not results:
                return False, "пустой ответ AddResults при создании кампании"
            err = self._first_error(results[0])
            if err:
                return False, err
            campaign_id = results[0].get("Id")
            if not campaign_id:
                return False, "Директ не вернул Id новой кампании"
            return True, str(campaign_id)
        except DirectWriteError as e:
            return False, str(e)
        except Exception as e:
            logger.exception("create_campaign failed")
            return False, f"неожиданная ошибка: {e}"

    async def create_ad_group(self, campaign_direct_id: str, name: str, region_ids: Optional[list[int]] = None) -> tuple[bool, str]:
        """Создаёт группу объявлений в новой кампании. region_ids по умолчанию
        [225] (Россия целиком) — для более узкого таргетинга нужно передать
        конкретные регионы явно."""
        try:
            result = await self._post("adgroups", "add", {
                "AdGroups": [{
                    "Name": name[:255],
                    "CampaignId": int(campaign_direct_id),
                    "RegionIds": region_ids or [225],
                }],
            })
            results = result.get("AddResults", [])
            if not results:
                return False, "пустой ответ AddResults при создании группы"
            err = self._first_error(results[0])
            if err:
                return False, err
            ag_id = results[0].get("Id")
            if not ag_id:
                return False, "Директ не вернул Id новой группы"
            return True, str(ag_id)
        except DirectWriteError as e:
            return False, str(e)
        except Exception as e:
            logger.exception("create_ad_group failed")
            return False, f"неожиданная ошибка: {e}"

    async def add_text_ad(self, ad_group_direct_id: str, title: str, title2: str, text: str, href: str) -> tuple[bool, str]:
        """Создаёт одно текстовое объявление в группе. Уходит на модерацию —
        показов не будет, пока Директ не примет объявление (это нормально,
        не ошибка apply)."""
        try:
            ad_payload = {
                "AdGroupId": int(ad_group_direct_id),
                "TextAd": {
                    "Title": title[:56],
                    "Text": text[:81],
                    "Href": href,
                },
            }
            if title2:
                ad_payload["TextAd"]["Title2"] = title2[:30]
            result = await self._post("ads", "add", {"Ads": [ad_payload]})
            results = result.get("AddResults", [])
            if not results:
                return False, "пустой ответ AddResults при создании объявления"
            err = self._first_error(results[0])
            if err:
                return False, err
            return True, str(results[0].get("Id", ""))
        except DirectWriteError as e:
            return False, str(e)
        except Exception as e:
            logger.exception("add_text_ad failed")
            return False, f"неожиданная ошибка: {e}"

    async def create_full_campaign(self, draft: dict) -> dict:
        """
        Оркестрирует создание кампании целиком из черновика (см.
        app/generators/campaign_planner.py): campaigns.add → для каждой
        группы adgroups.add → keywords.add → ads.add. Best-effort: если одна
        группа падает, остальные всё равно пробуются: отчёт возвращает
        детальный статус по каждому шагу, чтобы не потерять частичный успех.
        """
        report = {"campaign_id": None, "campaign_ok": False, "groups": [], "detail": ""}

        ok, camp_result = await self.create_campaign(draft["name"], float(draft.get("daily_budget_rub", 300)))
        if not ok:
            report["detail"] = f"Кампания не создана: {camp_result}"
            return report

        campaign_id = camp_result
        report["campaign_id"] = campaign_id
        report["campaign_ok"] = True

        for group in draft.get("ad_groups", []):
            g_report = {"name": group.get("name"), "ok": False, "ad_group_id": None, "detail": ""}
            ok, ag_result = await self.create_ad_group(campaign_id, group.get("name", "Группа"))
            if not ok:
                g_report["detail"] = f"Группа не создана: {ag_result}"
                report["groups"].append(g_report)
                continue

            ad_group_id = ag_result
            g_report["ad_group_id"] = ad_group_id

            keywords = group.get("keywords") or []
            if keywords:
                kw_ok, kw_detail = await self.add_keywords(ad_group_id, keywords)
                g_report["keywords_detail"] = kw_detail
            negatives = group.get("negative_keywords") or []
            if negatives:
                neg_ok, neg_detail = await self.add_negative_keywords(ad_group_id, negatives)
                g_report["negatives_detail"] = neg_detail

            ads = group.get("ads") or []
            ad_results = []
            for ad in ads[:1]:  # v1.7.0: одно объявление на группу для первого прогона
                ad_ok, ad_detail = await self.add_text_ad(
                    ad_group_id, ad.get("title", ""), ad.get("title2", ""),
                    ad.get("text", ""), ad.get("href", ""),
                )
                ad_results.append({"ok": ad_ok, "detail": ad_detail})
            g_report["ads_detail"] = ad_results
            g_report["ok"] = True
            report["groups"].append(g_report)

        return report

    # ── Остановка ключа ──────────────────────────────────────────────

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
