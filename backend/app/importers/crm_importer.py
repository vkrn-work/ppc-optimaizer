"""
Импорт выгрузки из CRM (CSV/XLSX) в таблицу leads.

Воронка:
  - lead — любая импортированная строка с заявкой;
  - mql  — всё, кроме спама/теста/дубля;
  - sql  — дошло минимум до КП/БП, независимо от исхода.
Классификация и матчинг живут в app/importers/lead_attribution.py — тот же код
используется при пересчёте атрибуции уже импортированных заявок.

v1.7.5 — три причины, по которым часть выгрузки молча не доезжала до анализа:

  1. КОЛОНКИ искались ТОЧНЫМ равенством заголовка одному из алиасов.
     Заголовок «Рекламная кампания», «utm_campaign (первый)», «Дата создания
     сделки» НЕ распознавался — колонка считалась отсутствующей. Для
     utm_campaign это означало campaign_id=NULL по ВСЕЙ выгрузке и
     полное отключение запасного уровня атрибуции.
     Теперь: точное совпадение → вхождение подстроки → префикс.
     Распознанные колонки возвращаются в ответе (columns_detected) —
     видно, что именно система поняла в файле, а что проигнорировала.

  2. ДАТА парсилась пятью форматами, среди которых не было ни
     '%d.%m.%Y %H:%M' (типовой формат Роистата/1С), ни ISO с 'T', ни
     Excel-серийного числа. При неудаче МОЛЧА подставлялся utcnow() —
     все заявки становились сегодняшними, попадали в ЛЮБОЙ период
     анализа и раздували счётчики за 28 дней. Теперь неудачные разборы
     считаются и возвращаются в ответе (date_parse_failed).

  3. ДЕДУПЛИКАЦИЯ работала только по external_id. Строки без него при
     повторной загрузке того же файла дублировались. Добавлен
     fingerprint (статус + источник + term + дата + выручка).

v1.7.6 — атрибуция стала иерархической (кампания→группа→ключ), attribute()
возвращает 4 значения, заявка получает ad_group_id. После импорта (в т.ч. при
0 новых строк) запускается пересчёт разноски по свежим данным Директа.
"""
import csv
import hashlib
import io
import logging
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Lead, LeadStatus
from app.importers.lead_attribution import (
    build_matchers, attribute, classify_status, parse_source_chain, normalize,
)

logger = logging.getLogger(__name__)


class CRMImportError(Exception):
    pass


# ─── Определение колонок ────────────────────────────────

# Порядок алиасов внутри списка значим: более специфичные — выше.
COLUMN_ALIASES: dict[str, list[str]] = {
    "external_id": ["external_id", "id сделки", "№ сделки", "№ заявки",
                    "номер заявки", "lead_id", "id"],
    "status": ["status", "статус", "стадия", "stage", "этап"],
    # "Источник" в реальных выгрузках — вся цепочка
    # кабинет → площадка → кампания → группа → id объявления → фраза.
    "source_chain": ["источник трафика", "источник", "source", "utm_source", "маркер"],
    "utm_term": ["utm_term", "поисковый запрос", "ключевое слово", "keyword",
                 "фраза", "ключ", "запрос"],
    "utm_campaign": ["utm_campaign", "рекламная кампания", "название кампании",
                     "кампания", "campaign"],
    "client_id": ["client_id", "clientid", "_ym_uid", "ym_client_id",
                  "clientid яндекс.метрика", "clientid яндекс метрика",
                  "yandex client id"],
    # yclid — НЕ то же самое, что ClientID: это id КЛИКА. Раньше он ложился
    # в ту же колонку client_id и терялся. Храним отдельно — это самый
    # точный ключ атрибуции, когда в проекте появится сбор Logs API.
    "yclid": ["yclid", "яндекс yclid", "click_id"],
    "revenue": ["сумма сделки", "выручка", "revenue", "доход", "сумма"],
    "created_at": ["created_at", "дата создания", "дата заявки", "дата", "date"],
}


def _build_column_map(headers: list) -> tuple[dict[str, int], dict[str, str]]:
    """Сопоставляет канонические поля с индексами колонок.

    Три уровня, от строгого к мягкому. Раньше был только первый, и любой
    заголовок с уточнением («Рекламная кампания») просто не виделся.
    Возвращает (col_map, detected) — detected уходит в ответ импорта.
    """
    norm_headers = [normalize(h) for h in headers]
    col_map: dict[str, int] = {}
    detected: dict[str, str] = {}
    taken: set[int] = set()

    def claim(canonical: str, idx: int):
        col_map[canonical] = idx
        detected[canonical] = str(headers[idx])
        taken.add(idx)

    # 1) точное совпадение
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in norm_headers:
                idx = norm_headers.index(alias)
                if idx not in taken:
                    claim(canonical, idx)
                    break

    # 2) алиас как подстрока заголовка («Рекламная кампания» ⊃ «кампания»)
    for canonical, aliases in COLUMN_ALIASES.items():
        if canonical in col_map:
            continue
        for alias in aliases:
            hit = next((i for i, h in enumerate(norm_headers)
                        if i not in taken and alias in h), None)
            if hit is not None:
                claim(canonical, hit)
                break

    # 3) заголовок как подстрока алиаса («камп.» ⊂ «кампания»)
    for canonical, aliases in COLUMN_ALIASES.items():
        if canonical in col_map:
            continue
        for alias in aliases:
            hit = next((i for i, h in enumerate(norm_headers)
                        if i not in taken and len(h) >= 4 and h in alias), None)
            if hit is not None:
                claim(canonical, hit)
                break

    return col_map, detected


# ─── Парсинг значений ────────────────────────────────────

def _parse_decimal(val) -> Optional[Decimal]:
    if val is None or val == "":
        return None
    if isinstance(val, (int, float, Decimal)):
        try:
            return Decimal(str(val))
        except InvalidOperation:
            return None
    s = str(val).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    s = s.replace("₽", "").replace("руб.", "").replace("руб", "")
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


# Расширенный список. Критичны '%d.%m.%Y %H:%M' (Роистат/1С) и ISO с 'T' —
# их отсутствие раньше сбрасывало всю выгрузку на сегодняшнюю дату.
DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
    "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
    "%d-%m-%Y", "%m/%d/%Y",
)


def _parse_date(val) -> Optional[datetime]:
    """None при неудаче — вызывающий код СЧИТАЕТ такие случаи,
    а не молча подставляет сегодняшнее число."""
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val
    # Excel хранит даты серийным числом от 1899-12-30
    if isinstance(val, (int, float)) and 20000 < float(val) < 60000:
        return datetime(1899, 12, 30) + timedelta(days=float(val))
    s = str(val).strip()
    if not s:
        return None
    s = s.split("+")[0].strip()  # отбрасываем таймзону вида '+03:00'
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _fingerprint(*parts) -> str:
    raw = "|".join(normalize(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# ─── Чтение файла (CSV / XLSX) ──────────────────────────────

def _read_rows(filename: str, content: bytes) -> tuple[list, list]:
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
        return all_rows[0], all_rows[1:]

    raise CRMImportError(f"Неподдерживаемый формат файла: {filename}")


# ─── Основная функция импорта ───────────────────────────

async def import_crm_file(db: AsyncSession, account_id: int, filename: str, content: bytes) -> dict:
    headers, rows = _read_rows(filename, content)
    col_map, detected = _build_column_map(headers)

    if "status" not in col_map:
        raise CRMImportError(
            "Не найдена колонка со статусом заявки. Ожидались заголовки: "
            + ", ".join(COLUMN_ALIASES["status"])
            + ". Фактические заголовки файла: " + ", ".join(str(h) for h in headers)
        )

    def get(row: list, canonical: str):
        idx = col_map.get(canonical)
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    matchers = await build_matchers(db, account_id)

    existing_q = await db.execute(
        select(Lead.external_id, Lead.raw_status, Lead.source_raw, Lead.utm_term,
               Lead.created_at, Lead.revenue)
        .where(Lead.account_id == account_id)
    )
    existing_external_ids: set = set()
    existing_fingerprints: set = set()
    for ext, raw_status, source_raw, utm_term, created, revenue in existing_q.all():
        if ext:
            existing_external_ids.add(ext)
        existing_fingerprints.add(_fingerprint(
            raw_status, source_raw, utm_term,
            created.date().isoformat() if created else "", revenue,
        ))

    stats = {
        "total_rows": len(rows),
        "imported": 0,
        "skipped_empty_status": 0,
        "skipped_duplicate": 0,
        "matched_by_ad_id": 0,
        "matched_by_search_query": 0,
        "matched_by_phrase": 0,
        "matched_by_ad_group": 0,
        "matched_by_campaign_only": 0,
        "unmatched": 0,
        "mql_count": 0,
        "sql_count": 0,
        # v1.7.5: диагностика, без которой тихие потери неотличимы от нормы
        "date_parse_failed": 0,
        "columns_detected": detected,
        "columns_ignored": [str(h) for i, h in enumerate(headers)
                            if i not in set(col_map.values())],
        "matchers": matchers.stats(),
    }

    match_stat_key = {
        "ad_id": "matched_by_ad_id",
        "search_query": "matched_by_search_query",
        "phrase": "matched_by_phrase",
        "ad_group": "matched_by_ad_group",
        "campaign": "matched_by_campaign_only",
    }

    for row in rows:
        raw_status = get(row, "status")
        if not raw_status or str(raw_status).strip() == "":
            stats["skipped_empty_status"] += 1
            continue
        is_mql, is_sql = classify_status(raw_status)

        external_id = get(row, "external_id")
        external_id = str(external_id).strip() if external_id not in (None, "") else None
        if external_id and external_id in existing_external_ids:
            stats["skipped_duplicate"] += 1
            continue

        source_chain_raw = get(row, "source_chain")
        parsed = parse_source_chain(source_chain_raw)

        explicit_term = get(row, "utm_term")
        term = (str(explicit_term).strip() if explicit_term not in (None, "") else None) \
            or parsed.get("term")
        campaign_name = get(row, "utm_campaign")

        parsed_date = _parse_date(get(row, "created_at"))
        if parsed_date is None and col_map.get("created_at") is not None:
            stats["date_parse_failed"] += 1
        created_at = parsed_date or datetime.utcnow()
        revenue = _parse_decimal(get(row, "revenue"))

        # Дедуп без external_id — по содержанию строки.
        fp = _fingerprint(raw_status, source_chain_raw, term,
                          created_at.date().isoformat(), revenue)
        if not external_id and fp in existing_fingerprints:
            stats["skipped_duplicate"] += 1
            continue

        keyword_id, ad_group_id, campaign_id, matched_by = attribute(
            matchers,
            ad_id=parsed.get("ad_id"),
            term=term,
            campaign_name=campaign_name,
            chain_campaign=parsed.get("campaign"),
            chain_ad_group=parsed.get("ad_group"),
        )
        if matched_by:
            stats[match_stat_key[matched_by]] += 1
        else:
            stats["unmatched"] += 1

        client_id = get(row, "client_id")
        client_id = str(client_id).strip() if client_id not in (None, "") else None

        lead = Lead(
            account_id=account_id,
            external_id=external_id,
            status=LeadStatus.sql if is_sql else LeadStatus.lead,
            raw_status=str(raw_status).strip()[:255],
            is_mql=is_mql,
            is_sql=is_sql,
            keyword_id=keyword_id,
            ad_group_id=ad_group_id,
            campaign_id=campaign_id,
            matched_by=matched_by,
            matched_ad_id=parsed.get("ad_id"),
            client_id=client_id,
            utm_source=parsed.get("platform"),
            utm_campaign=str(campaign_name or parsed.get("campaign") or "")[:255] or None,
            utm_term=str(term)[:500] if term else None,
            source_raw=str(source_chain_raw) if source_chain_raw else None,
            revenue=revenue,
            created_at=created_at,
        )
        db.add(lead)
        stats["imported"] += 1
        if is_mql:
            stats["mql_count"] += 1
        if is_sql:
            stats["sql_count"] += 1
        if external_id:
            existing_external_ids.add(external_id)
        existing_fingerprints.add(fp)

    await db.commit()

    # v1.7.6: даже если новых строк 0 (повторная загрузка того же файла) —
    # перематчиваем уже лежащие заявки по свежим данным Директа. Так «обновить
    # файл» перестаёт быть бесполезным действием: разноска пересчитывается.
    from app.importers.lead_attribution import reattribute_account
    try:
        stats["reattribution"] = await reattribute_account(db, account_id)
    except Exception as e:
        logger.warning(f"post-import reattribution failed: {e}")
        stats["reattribution"] = {"error": str(e)}

    # Человекочитаемый итог — чтобы «Импортировано: 0» не читалось как поломка.
    dup = stats["skipped_duplicate"]
    if stats["imported"] == 0 and dup > 0:
        stats["message"] = (
            f"Новых заявок нет: все {dup} строк уже были загружены ранее "
            f"(дубли по номеру сделки/содержимому). Разноска существующих "
            f"заявок пересчитана заново."
        )
    else:
        stats["message"] = (
            f"Загружено новых заявок: {stats['imported']}. "
            f"Пропущено дублей: {dup}."
        )

    logger.info(f"CRM import account={account_id} file={filename}: {stats}")
    return stats
