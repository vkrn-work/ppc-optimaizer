"""
Импорт выгрузки из CRM (CSV/XLSX) в таблицу leads.

Этот модуль отсутствовал на GitHub, хотя routes.py уже вызывал
`from app.importers.crm_importer import import_crm_file, CRMImportError` —
эндпоинт POST /accounts/{id}/crm-import был нерабочим (ModuleNotFoundError).

Логика воронки (по итогам разбора реальной выгрузки gto365.ru):
  - lead — вообще любая импортированная строка с заявкой.
  - mql  — всё, кроме спама/теста/явного мусора. По умолчанию ВСЕ статусы
           считаются MQL, если явно не помечены как "junk" (см. JUNK_MARKERS).
  - sql  — статусы, означающие что заявка дошла минимум до стадии
           коммерческого предложения (КП / БП), независимо от исхода
           (в т.ч. "Не прошло КП" — это тоже SQL, просто не выигранный).

Матчинг с Директом — каскад из четырёх уровней, от точного к грубому (v1.7.4).
Прежняя версия имела только уровни 1 и 3, и на боевых данных теряла 73% заявок:

  1. По номеру объявления (ad_id) из цепочки "Источник" — сверяется с
     KeywordStat.ad_id. ВНИМАНИЕ: сейчас этот уровень нерабочий, потому что
     коллектор берёт CRITERIA_PERFORMANCE_REPORT, где поля AdId нет —
     KeywordStat.ad_id пуст во всех 7838 строках. Код оставлен: заработает
     сам, как только ad_id начнёт собираться (нужен AD_PERFORMANCE_REPORT).
  2. По поисковому запросу через таблицу search_queries. Ключевой момент:
     utm_term из CRM — это то, что ВВЁЛ ПОЛЬЗОВАТЕЛЬ ("цена на хардокс"), а не
     фраза ключа из Директа ("Износостойкая сталь +Хардокс -Аналоги").
     search_queries связывает одно с другим, и это основной рабочий уровень.
  3. По точному совпадению utm_term с Keyword.phrase — исторический уровень,
     срабатывает редко и только когда запрос случайно совпал с фразой.
  4. По кампании (utm_campaign → Campaign.name). Ключ не определён, но заявка
     всё равно участвует в анализе на уровне кампании. Без этого уровня
     кампания с реальными продажами выглядела нулевой и попадала под
     сокращение — именно так и произошло с Hardox на боевом прогоне.

ClientID Яндекс.Метрики сохраняется в leads.client_id на будущее — сейчас
матчинг по нему не выполняется, потому что коллектор Метрики забирает только
агрегированные срезы (Stat API), а не визиты с ClientID (для этого нужен
отдельный сбор через Logs API, которого в проекте пока нет).
"""
import csv
import io
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Lead, LeadStatus, Keyword, KeywordStat, SearchQuery, Campaign

logger = logging.getLogger(__name__)


class CRMImportError(Exception):
    pass


# ─── Определение колонок ──────────────────────────────────

COLUMN_ALIASES: dict[str, list[str]] = {
    "external_id": ["external_id", "id", "id сделки", "№ заявки", "номер заявки",
                     "заявка", "lead_id", "№ сделки"],
    "status": ["status", "статус", "стадия", "stage"],
    # "Источник" в реальных выгрузках — не чистый utm_source, а вся цепочка
    # кабинет → площадка → кампания → группа → id объявления → фраза.
    "source_chain": ["источник", "source", "utm_source"],
    "utm_term": ["utm_term", "ключевое слово", "keyword", "фраза", "ключ"],
    "utm_campaign": ["utm_campaign", "кампания"],
    "client_id": ["client_id", "clientid", "yclid", "_ym_uid", "ym_client_id",
                   "clientid яндекс.метрика", "yandex client id", "clientid яндекс метрика"],
    "revenue": ["revenue", "сумма", "бюджет", "доход", "сумма сделки", "выручка"],
    "created_at": ["created_at", "дата", "date", "дата создания", "дата заявки"],
}


def _normalize_header(h: object) -> str:
    if h is None:
        return ""
    s = str(h).strip().lower().replace("ё", "е")
    s = re.sub(r"\s+", " ", s)
    return s


def _build_column_map(headers: list) -> dict[str, int]:
    norm_headers = [_normalize_header(h) for h in headers]
    col_map: dict[str, int] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in norm_headers:
                col_map[canonical] = norm_headers.index(alias)
                break
    return col_map


# ─── Классификация статусов: lead / mql / sql ─────────────────────

# Статусы, дошедшие минимум до КП/БП — независимо от исхода.
SQL_STATUS_SET = {
    "кп", "не прошло кп", "запущен бп", "бп", "заказ запущен",
    "сделка", "продажа", "выигран", "won", "deal", "proposal", "предложение",
}

# Явный мусор — не считается даже MQL (в реальной выгрузке таких строк не
# встретилось, но оставляем на будущее).
JUNK_MARKERS = ["спам", "spam", "тест", "test", "фрод", "fraud"]


def _classify_status(raw_status: Optional[str]) -> tuple[bool, bool]:
    """Возвращает (is_mql, is_sql) по тексту статуса из CRM."""
    norm = _normalize_header(raw_status)
    if not norm:
        return False, False
    if any(m in norm for m in JUNK_MARKERS):
        return False, False
    is_sql = norm in SQL_STATUS_SET
    return True, is_sql


def _parse_status_for_enum(is_sql: bool) -> LeadStatus:
    return LeadStatus.sql if is_sql else LeadStatus.lead


# ─── Парсинг цепочки "Источник" ────────────────────────────

def parse_source_chain(raw: Optional[str]) -> dict:
    """
    Разбирает строку вида:
      "ГТО 4 → Поиск → Спецстали_Quard_все /gto365.ru /РФ3 → ! Quard → 17223320102 → квард"
    на кабинет / площадку / кампанию / группу / id объявления / фразу.

    Для РСЯ (нет условия показа) цепочка обычно заканчивается номером
    объявления без фразы — это тоже поддерживается.
    """
    result = {
        "cabinet": None, "platform": None, "campaign": None,
        "ad_group": None, "ad_id": None, "term": None,
    }
    if not raw:
        return result
    parts = [p.strip() for p in str(raw).split("→") if p.strip()]
    if not parts:
        return result

    if len(parts) >= 1:
        result["cabinet"] = parts[0]
    if len(parts) >= 2:
        result["platform"] = parts[1]
    if len(parts) >= 3:
        result["campaign"] = parts[2]
    if len(parts) >= 4:
        result["ad_group"] = parts[3]

    if len(parts) >= 5:
        if parts[-1].isdigit():
            # РСЯ: цепочка заканчивается id объявления, фразы нет
            result["ad_id"] = parts[-1]
        elif parts[-2].isdigit():
            # Поиск: ... → id объявления → фраза
            result["ad_id"] = parts[-2]
            result["term"] = parts[-1]
        else:
            # id объявления не найден (бывает, если в цепочке заголовок
            # объявления вместо номера) — берём последний сегмент как фразу
            result["term"] = parts[-1]

    return result


# ─── Парсинг значений ─────────────────────────────────────

def _parse_decimal(val) -> Optional[Decimal]:
    if val is None or val == "":
        return None
    if isinstance(val, (int, float, Decimal)):
        try:
            return Decimal(str(val))
        except InvalidOperation:
            return None
    s = str(val).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _parse_date(val) -> Optional[datetime]:
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val
    s = str(val).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# ─── Чтение файла (CSV / XLSX) ───────────────────────────────

def _read_rows(filename: str, content: bytes) -> tuple[list, list]:
    """Возвращает (headers, rows) как список списков (без заголовка)."""
    lower = filename.lower()
    if lower.endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        all_rows = list(ws.iter_rows(values_only=True))
        if not all_rows:
            raise CRMImportError("Файл пустой")
        headers = list(all_rows[0])
        rows = [list(r) for r in all_rows[1:] if any(c is not None and c != "" for c in r)]
        return headers, rows

    if lower.endswith(".csv"):
        text = None
        for enc in ("utf-8-sig", "cp1251", "utf-8"):
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise CRMImportError("Не удалось определить кодировку CSV (пробовали utf-8, cp1251)")
        sample = text[:2000]
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        all_rows = [r for r in reader if any(c.strip() for c in r)]
        if not all_rows:
            raise CRMImportError("Файл пустой")
        headers = all_rows[0]
        rows = all_rows[1:]
        return headers, rows

    raise CRMImportError(f"Неподдерживаемый формат файла: {filename}")


# ─── Основная функция импорта ──────────────────────────────

async def import_crm_file(db: AsyncSession, account_id: int, filename: str, content: bytes) -> dict:
    headers, rows = _read_rows(filename, content)
    col_map = _build_column_map(headers)

    if "status" not in col_map:
        raise CRMImportError(
            "Не найдена колонка со статусом заявки (ожидались заголовки: "
            + ", ".join(COLUMN_ALIASES["status"]) + ")"
        )

    def get(row: list, canonical: str):
        idx = col_map.get(canonical)
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    kw_q = await db.execute(select(Keyword.id, Keyword.phrase).where(Keyword.account_id == account_id))
    phrase_to_id = {_normalize_header(phrase): kw_id for kw_id, phrase in kw_q.all()}

    adid_q = await db.execute(
        select(KeywordStat.ad_id, KeywordStat.keyword_id)
        .where(KeywordStat.account_id == account_id, KeywordStat.ad_id.isnot(None))
        .distinct()
    )
    ad_id_to_kw: dict = {}
    for ad_id, kw_id in adid_q.all():
        ad_id_to_kw.setdefault(str(ad_id), kw_id)

    # v1.7.4: utm_term из CRM — это ПОИСКОВЫЙ ЗАПРОС пользователя ("цена на
    # хардокс"), а не фраза ключа из Директа ("Износостойкая сталь +Хардокс
    # -Аналоги"). Сверять их напрямую бессмысленно: на реальной выгрузке так
    # совпало лишь 15 строк из 56. Правильный мост — таблица search_queries,
    # которая связывает запрос с ключом, по которому он был показан.
    sq_q = await db.execute(
        select(SearchQuery.query, SearchQuery.keyword_id)
        .where(SearchQuery.account_id == account_id, SearchQuery.keyword_id.isnot(None))
        .distinct()
    )
    query_to_kw: dict = {}
    for query_text, kw_id in sq_q.all():
        query_to_kw.setdefault(_normalize_header(query_text), kw_id)

    # v1.7.4: запасной уровень атрибуции — кампания. utm_campaign заполнен
    # практически всегда и совпадает с названием кампании в Директе один в
    # один. Без этого лиды, не привязавшиеся к ключу, пропадали из анализа
    # целиком — и кампания с реальными заявками выглядела как нулевая.
    camp_q = await db.execute(
        select(Campaign.id, Campaign.name).where(Campaign.account_id == account_id)
    )
    campaign_name_to_id = {_normalize_header(name): cid for cid, name in camp_q.all()}

    existing_q = await db.execute(
        select(Lead.external_id).where(Lead.account_id == account_id, Lead.external_id.isnot(None))
    )
    existing_external_ids = {row[0] for row in existing_q.all()}

    stats = {
        "total_rows": len(rows),
        "imported": 0,
        "skipped_empty_status": 0,
        "skipped_duplicate": 0,
        "matched_by_ad_id": 0,
        "matched_by_search_query": 0,
        "matched_by_phrase": 0,
        "matched_by_campaign_only": 0,
        "unmatched": 0,
        "mql_count": 0,
        "sql_count": 0,
    }

    for row in rows:
        raw_status = get(row, "status")
        if not raw_status or str(raw_status).strip() == "":
            stats["skipped_empty_status"] += 1
            continue
        is_mql, is_sql = _classify_status(raw_status)

        external_id = get(row, "external_id")
        external_id = str(external_id).strip() if external_id not in (None, "") else None
        if external_id and external_id in existing_external_ids:
            stats["skipped_duplicate"] += 1
            continue

        source_chain_raw = get(row, "source_chain")
        parsed = parse_source_chain(source_chain_raw)

        explicit_term = get(row, "utm_term")
        term = (str(explicit_term).strip() if explicit_term not in (None, "") else None) or parsed.get("term")

        # v1.7.4: каскад атрибуции от точного к грубому. Раньше было только два
        # верхних уровня, и оба почти не работали: ad_id пуст (коллектор берёт
        # CRITERIA_PERFORMANCE_REPORT, где поля AdId нет), а phrase сверяла
        # поисковый запрос с фразой ключа. Итог — 73% лидов терялись.
        keyword_id = None
        matched_by = None
        norm_term = _normalize_header(term) if term else None

        if parsed.get("ad_id") and parsed["ad_id"] in ad_id_to_kw:
            keyword_id = ad_id_to_kw[parsed["ad_id"]]
            matched_by = "ad_id"
            stats["matched_by_ad_id"] += 1
        elif norm_term and norm_term in query_to_kw:
            keyword_id = query_to_kw[norm_term]
            matched_by = "search_query"
            stats["matched_by_search_query"] += 1
        elif norm_term and norm_term in phrase_to_id:
            keyword_id = phrase_to_id[norm_term]
            matched_by = "phrase"
            stats["matched_by_phrase"] += 1

        client_id = get(row, "client_id")
        client_id = str(client_id).strip() if client_id not in (None, "") else None

        campaign = get(row, "utm_campaign") or parsed.get("campaign")
        campaign_id = campaign_name_to_id.get(_normalize_header(campaign)) if campaign else None

        # Ключ не определился, но кампания известна — лид всё равно попадёт в
        # анализ на уровне кампании, а не исчезнет.
        if keyword_id is None:
            if campaign_id is not None:
                matched_by = "campaign"
                stats["matched_by_campaign_only"] += 1
            else:
                stats["unmatched"] += 1

        lead = Lead(
            account_id=account_id,
            external_id=external_id,
            status=_parse_status_for_enum(is_sql),
            raw_status=str(raw_status).strip()[:255],
            is_mql=is_mql,
            is_sql=is_sql,
            keyword_id=keyword_id,
            campaign_id=campaign_id,
            matched_by=matched_by,
            matched_ad_id=parsed.get("ad_id"),
            client_id=client_id,
            utm_source=parsed.get("platform"),
            utm_campaign=str(campaign)[:255] if campaign else None,
            utm_term=str(term)[:500] if term else None,
            source_raw=str(source_chain_raw) if source_chain_raw else None,
            revenue=_parse_decimal(get(row, "revenue")),
            created_at=_parse_date(get(row, "created_at")) or datetime.utcnow(),
        )
        db.add(lead)
        stats["imported"] += 1
        if is_mql:
            stats["mql_count"] += 1
        if is_sql:
            stats["sql_count"] += 1
        if external_id:
            existing_external_ids.add(external_id)

    await db.commit()
    logger.info(f"CRM import account={account_id} file={filename}: {stats}")
    return stats
