"""Тесты чистых функций атрибуции и разбора CRM-выгрузки.

Покрывают ровно те места, где были баги v1.7.4. Запуск:
    cd backend && python -m pytest tests/ -q
БД не требуется — всё на синтетике.
"""
from datetime import datetime

import pytest

from app.importers.lead_attribution import (
    Matchers, attribute, classify_status, normalize, normalize_campaign,
    parse_source_chain, resolve_campaign_id,
)
from app.importers.crm_importer import _build_column_map, _parse_date, _parse_decimal


# ─── Статусы ──────────────────────────────────

@pytest.mark.parametrize("status,expect_mql,expect_sql", [
    ("КП", True, True),
    ("Не прошло КП", True, True),
    ("КП отправлено 12.05", True, True),
    ("Запущен БП №7", True, True),
    ("Заказ запущен", True, True),
    ("В работе", True, False),
    ("Новая заявка", True, False),
    ("Спам", False, False),
    ("тестовая заявка", False, False),
    ("", False, False),
    (None, False, False),
])
def test_classify_status(status, expect_mql, expect_sql):
    assert classify_status(status) == (expect_mql, expect_sql)


# ─── Цепочка «Источник» ──────────────────────

CHAIN_ARROW = ("ГТО 4 → Поиск → Спецстали_Quard_все /gto365.ru /РФ3 → "
               "! Quard → 17223320102 → квард")


def test_parse_source_chain_arrow():
    r = parse_source_chain(CHAIN_ARROW)
    assert r["campaign"] == "Спецстали_Quard_все /gto365.ru /РФ3"
    assert r["ad_group"] == "! Quard"
    assert r["ad_id"] == "17223320102"
    assert r["term"] == "квард"


def test_parse_source_chain_rsya_ends_with_ad_id():
    r = parse_source_chain("ГТО 4 → РСЯ → Кампания → Группа → 987654321")
    assert r["ad_id"] == "987654321"
    assert r["term"] is None


def test_parse_source_chain_alternative_separator():
    r = parse_source_chain("ГТО 4 » Поиск » Кампания X » Группа » 111 » фраза")
    assert r["campaign"] == "Кампания X"
    assert r["ad_id"] == "111"
    assert r["term"] == "фраза"


def test_parse_source_chain_empty():
    assert parse_source_chain(None)["campaign"] is None
    assert parse_source_chain("просто текст")["campaign"] is None


# ─── Нормализация ───────────────────────────

def test_normalize_handles_nbsp_and_yo():
    assert normalize("  Ключёвое\xa0СЛОВО  ") == "ключевое слово"


def test_normalize_campaign_strips_service_tail():
    assert normalize_campaign("Спецстали_Quard_все /gto365.ru /РФ3") == "спецстали quard все"


# ─── Матчинг кампании ──────────────────────

def _matchers() -> Matchers:
    m = Matchers()
    m.kw_to_campaign = {10: 100, 20: 200}
    m.kw_to_adgroup = {10: 1000, 20: 2000}
    m.phrase_to_kw = {"износостойкая сталь +хардокс": 10}
    m.query_to_kw = {"цена на хардокс": 10, "квард": 20}
    m.ad_id_to_kw = {"17223320102": 20}
    m.campaign_exact = {"спецстали_quard_все /gto365.ru /рф3": 200}
    m.campaign_loose = {"спецстали quard все": 200}
    # группа "! Quard" внутри кампании 200 (Спецстали_Quard_все)
    m.adgroup_exact = {(200, "! quard"): 1500}
    m.adgroup_loose = {(200, "quard"): 1500}
    m.adgroup_campaign = {1000: 100, 2000: 200, 1500: 200}
    return m


def test_resolve_campaign_exact_then_loose():
    m = _matchers()
    assert resolve_campaign_id(m, "Спецстали_Quard_все /gto365.ru /РФ3") == 200
    assert resolve_campaign_id(m, "Спецстали-Quard-все") == 200
    assert resolve_campaign_id(m, "Нет такой") is None


def test_campaign_loose_collision_is_not_matched():
    m = Matchers()
    m.campaign_loose = {"кампания": None}
    assert resolve_campaign_id(m, "Кампания 1") is None


# ─── Каскад атрибуции (v1.7.6: 4-tuple keyword, ad_group, campaign, by) ─────

def test_attribute_by_ad_id_wins():
    kw, ag, camp, by = attribute(_matchers(), ad_id="17223320102", term="цена на хардокс")
    assert (kw, camp, by) == (20, 200, "ad_id")


def test_attribute_by_search_query():
    kw, ag, camp, by = attribute(_matchers(), term="Цена на Хардокс")
    assert (kw, camp, by) == (10, 100, "search_query")
    assert ag == 1000


def test_attribute_by_phrase():
    kw, ag, camp, by = attribute(_matchers(), term="Износостойкая сталь +Хардокс")
    assert (kw, camp, by) == (10, 100, "phrase")


def test_attribute_campaign_only():
    kw, ag, camp, by = attribute(_matchers(), term="неизвестный запрос",
                                 campaign_name="Спецстали_Quard_все /gto365.ru /РФ3")
    assert (kw, ag, camp, by) == (None, None, 200, "campaign")


def test_attribute_ad_group_from_chain_without_keyword():
    """v1.7.6 РСЯ: ключа нет, но группа из цепочки внутри кампании — есть."""
    kw, ag, camp, by = attribute(
        _matchers(),
        chain_campaign="Спецстали_Quard_все /gto365.ru /РФ3",
        chain_ad_group="! Quard",
    )
    assert kw is None
    assert ag == 1500
    assert camp == 200
    assert by == "ad_group"


def test_attribute_nothing():
    assert attribute(_matchers(), term="ничего") == (None, None, None, None)


def test_campaign_id_derived_from_keyword_when_name_mismatches():
    kw, ag, camp, by = attribute(_matchers(), term="цена на хардокс",
                                 campaign_name="Кампания переименована в Директе")
    assert kw == 10
    assert camp == 100
    assert by == "search_query"


def test_junk_statuses_not_mql():
    """v1.7.6: Дубль/Перекуп/Не наша номенклатура — не MQL (из выгрузки gto365)."""
    for junk in ("Дубль (уже в работе)", "Перекуп", "Не наша номенклатура"):
        assert classify_status(junk) == (False, False)


# ─── Распознавание колонок ───────────────────────────

def test_column_map_exact():
    headers = ["ID сделки", "Статус", "Источник", "utm_term", "utm_campaign", "Дата"]
    col_map, detected = _build_column_map(headers)
    assert col_map["status"] == 1
    assert col_map["source_chain"] == 2
    assert col_map["utm_campaign"] == 4
    assert col_map["created_at"] == 5
    assert detected["status"] == "Статус"


def test_column_map_substring_headers():
    """ГЛАВНЫЙ ФИКС импортера: в v1.7.4 сопоставление шло только точным
    равенством, и такие заголовки не распознавались вовсе."""
    headers = ["№ сделки", "Стадия сделки", "Источник трафика",
               "Поисковый запрос", "Рекламная кампания", "Дата создания"]
    col_map, _ = _build_column_map(headers)
    assert col_map["status"] == 1
    assert col_map["source_chain"] == 2
    assert col_map["utm_term"] == 3
    assert col_map["utm_campaign"] == 4
    assert col_map["created_at"] == 5


def test_column_map_does_not_reuse_same_index():
    headers = ["Статус", "Кампания"]
    col_map, _ = _build_column_map(headers)
    assert len(set(col_map.values())) == len(col_map)


# ─── Даты и числа ──────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("2026-05-14", datetime(2026, 5, 14)),
    ("2026-05-14 10:30:00", datetime(2026, 5, 14, 10, 30)),
    ("14.05.2026", datetime(2026, 5, 14)),
    ("14.05.2026 10:30", datetime(2026, 5, 14, 10, 30)),
    ("2026-05-14T10:30:00", datetime(2026, 5, 14, 10, 30)),
    ("2026-05-14T10:30:00+03:00", datetime(2026, 5, 14, 10, 30)),
])
def test_parse_date_formats(raw, expected):
    assert _parse_date(raw) == expected


def test_parse_date_returns_none_instead_of_today():
    assert _parse_date("не дата") is None
    assert _parse_date("") is None
    assert _parse_date(None) is None


def test_parse_date_excel_serial():
    assert _parse_date(45000).date() == datetime(2023, 3, 15).date()


@pytest.mark.parametrize("raw,expected", [
    ("1 200,50", "1200.50"),
    ("1\xa0200", "1200"),
    ("3000 ₽", "3000"),
    ("", None),
    (None, None),
    ("не число", None),
])
def test_parse_decimal(raw, expected):
    got = _parse_decimal(raw)
    assert (str(got) if got is not None else None) == expected
