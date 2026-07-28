"""
v1.7.6: единый модуль атрибуции заявок с ВЛОЖЕННОСТЬЮ кампания→группа→ключ.

Ключевая идея версии: цепочка «Источник» из Роистата содержит всю иерархию
ЯВНО, и она — источник истины, а не косвенные джойны:

    ГТО 4 → Поиск → Спецстали_Quard_все /gto365.ru /РФ3 → ! Quard → 17223320102 → квард
    кабинет  канал   КАМПАНИЯ                             ГРУППА    ОБЪЯВЛЕНИЕ    фраза

Поэтому матчинг идёт сверху вниз:
  1. campaign — по ИМЕНИ кампании из цепочки (то, что было на момент клика).
     Если имя не совпало (кампанию переименовали) — берём кампанию найденного
     ключа как запасной вариант.
  2. ad_group — по имени группы ВНУТРИ найденной кампании. Это и есть
     вложенность: заявка получает ad_group_id, даже если ключ не определился
     (важно для РСЯ/ретаргетинга — там ключей нет вообще).
  3. keyword — ad_id → search_query → phrase, как и раньше, но выбор ключа
     ограничен найденной группой, если она известна.

Что это чинит по сравнению с v1.7.5:
  A. РСЯ/ретаргетинг больше не теряется. У таких кампаний нет ключевых слов,
     поэтому keyword_id всегда NULL — но campaign_id и ad_group_id теперь
     проставляются из цепочки, и заявка доезжает до отчёта и до ИИ.
  B. Кампания больше НЕ переезжает вслед за ключом. Раньше «bisplate» из
     кампании Quard мог сматчиться на ключ в другой кампании и уехать туда.
     Теперь кампания фиксируется по цепочке, а ключ лишь уточняет внутри неё.
  C. Появляется ad_group_id — уровень, которого не было. Без него отчёт
     «По группам» по заявкам был пустой, и ИИ не видел срез групп.

Каскад matched_by (от точного к грубому): ad_id → search_query → phrase →
ad_group → campaign → None.
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Keyword, KeywordStat, SearchQuery, Campaign, AdGroup, Lead

logger = logging.getLogger(__name__)


# ─── Нормализация ──────────────────────────────────────────

def normalize(s: object) -> str:
    """Регистр, ё→е, неразрывные пробелы, кавычки-ёлочки, схлопывание пробелов."""
    if s is None:
        return ""
    t = str(s).replace("\xa0", " ").strip().lower().replace("ё", "е")
    t = t.replace("«", '"').replace("»", '"')
    t = re.sub(r"\s+", " ", t)
    return t


def normalize_campaign(s: object) -> str:
    """Агрессивная нормализация имени кампании/группы для запасного матчинга.

    Директологи кодируют служебные хвосты: «Спецстали_Quard_все /gto365.ru /РФ3».
    CRM отдаёт то же с другим разделителем, без домена или региона. Отрезаем
    всё после первого '/', разделители → пробел, выкидываем голые числа.
    """
    t = normalize(s)
    if not t:
        return ""
    t = t.split("/")[0]
    t = re.sub(r"[_\-|.,()\[\]!#]+", " ", t)
    t = re.sub(r"\b\d+\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# ─── Классификация статусов воронки ────────────────────────

# SQL — дошло минимум до КП/БП, независимо от исхода. Вхождение, а не равенство:
# «КП отправлено», «Запущен БП №7», «Не прошло КП» — всё это SQL.
SQL_MARKERS = (
    "кп", "бп", "заказ запущен", "сделка", "продажа", "выигран",
    "won", "deal", "proposal", "предложение", "счет", "договор",
)

# Явный мусор — НЕ MQL. По уточнению клиента: MQL = всё, КРОМЕ спама/теста и
# «приглашения к сотрудничеству / тендера». «Дубль» мусором НЕ считается —
# это может быть дубль заявки с продажей (значит трафик привёл хорошего лида,
# пусть и не первым касанием). «Перекуп» / «Не наша номенклатура» тоже
# остаются MQL — их клиент из мусора не исключал.
JUNK_MARKERS = (
    "спам", "spam", "тест", "test",
    "приглашение", "сотрудничеств", "тендер",
)


def classify_status(raw_status: Optional[str]) -> tuple[bool, bool]:
    """(is_mql, is_sql) по тексту статуса CRM."""
    norm = normalize(raw_status)
    if not norm:
        return False, False
    if any(m in norm for m in JUNK_MARKERS):
        return False, False
    tokens = set(re.findall(r"[a-zа-я]+", norm))
    is_sql = any((m in tokens) if len(m) <= 2 else (m in norm) for m in SQL_MARKERS)
    return True, is_sql


# ─── Парсинг цепочки «Источник» ────────────────────────────

CHAIN_SEPARATORS = ("→", "->", "»", "|", " / ", " — ", " – ")


def parse_source_chain(raw: Optional[str]) -> dict:
    """кабинет → площадка → кампания → группа → id объявления → фраза.

    channel — «Поиск» / «РСЯ» / ... (вторая позиция). Нужен, чтобы отличать
    поисковые кампании от сетевых даже до матчинга.
    """
    result = {"cabinet": None, "platform": None, "channel": None, "campaign": None,
              "ad_group": None, "ad_id": None, "term": None}
    if not raw:
        return result

    text = str(raw)
    sep = next((s for s in CHAIN_SEPARATORS if s in text), None)
    if sep is None:
        return result
    parts = [p.strip() for p in text.split(sep) if p.strip()]
    if not parts:
        return result

    if len(parts) > 0:
        result["cabinet"] = parts[0]
    if len(parts) > 1:
        result["platform"] = parts[1]
        result["channel"] = parts[1]
    if len(parts) > 2:
        result["campaign"] = parts[2]
    if len(parts) > 3:
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
            # последний сегмент — фраза (или заголовок объявления)
            result["term"] = parts[-1]
    return result


# ─── Справочники матчинга ──────────────────────────────────

@dataclass
class Matchers:
    """Предзагруженные словари. Строятся один раз на импорт/пересчёт."""
    ad_id_to_kw: dict = field(default_factory=dict)
    query_to_kw: dict = field(default_factory=dict)
    phrase_to_kw: dict = field(default_factory=dict)
    campaign_exact: dict = field(default_factory=dict)
    campaign_loose: dict = field(default_factory=dict)
    kw_to_campaign: dict = field(default_factory=dict)
    kw_to_adgroup: dict = field(default_factory=dict)
    # (campaign_id, normalized_group_name) → ad_group_id
    adgroup_exact: dict = field(default_factory=dict)
    adgroup_loose: dict = field(default_factory=dict)
    adgroup_campaign: dict = field(default_factory=dict)  # ad_group_id → campaign_id

    def stats(self) -> dict:
        return {
            "ad_ids": len(self.ad_id_to_kw),
            "search_queries": len(self.query_to_kw),
            "phrases": len(self.phrase_to_kw),
            "campaigns": len(self.campaign_exact),
            "ad_groups": len(self.adgroup_campaign),
            "keywords_with_campaign": len(self.kw_to_campaign),
        }


async def build_matchers(db: AsyncSession, account_id: int) -> Matchers:
    m = Matchers()

    # keyword → campaign / ad_group (+ фраза)
    kw_rows = await db.execute(
        select(Keyword.id, Keyword.phrase, Keyword.ad_group_id,
               AdGroup.campaign_id)
        .join(AdGroup, Keyword.ad_group_id == AdGroup.id)
        .where(Keyword.account_id == account_id)
    )
    for kw_id, phrase, ag_id, camp_id in kw_rows.all():
        m.kw_to_campaign[kw_id] = camp_id
        m.kw_to_adgroup[kw_id] = ag_id
        m.phrase_to_kw.setdefault(normalize(phrase), kw_id)

    orphan_rows = await db.execute(
        select(Keyword.id, Keyword.phrase).where(Keyword.account_id == account_id)
    )
    for kw_id, phrase in orphan_rows.all():
        m.phrase_to_kw.setdefault(normalize(phrase), kw_id)

    # ad_group → campaign, и (campaign, имя группы) → ad_group_id
    ag_rows = await db.execute(
        select(AdGroup.id, AdGroup.name, AdGroup.campaign_id)
        .where(AdGroup.account_id == account_id)
    )
    for ag_id, name, camp_id in ag_rows.all():
        m.adgroup_campaign[ag_id] = camp_id
        m.adgroup_exact.setdefault((camp_id, normalize(name)), ag_id)
        loose = normalize_campaign(name)
        if loose:
            key = (camp_id, loose)
            # коллизия loose-имени внутри кампании → не матчим, чтобы не соврать
            m.adgroup_loose[key] = None if key in m.adgroup_loose else ag_id

    # ad_id → keyword: побеждает объявление с наибольшим числом кликов
    ad_rows = await db.execute(
        select(KeywordStat.ad_id, KeywordStat.keyword_id,
               func.sum(KeywordStat.clicks).label("clicks"))
        .where(KeywordStat.account_id == account_id, KeywordStat.ad_id.isnot(None))
        .group_by(KeywordStat.ad_id, KeywordStat.keyword_id)
    )
    ad_best: dict = {}
    for ad_id, kw_id, clicks in ad_rows.all():
        key = str(ad_id)
        c = int(clicks or 0)
        if key not in ad_best or c > ad_best[key][1]:
            ad_best[key] = (kw_id, c)
    m.ad_id_to_kw = {k: v[0] for k, v in ad_best.items()}

    # поисковый запрос → keyword: ключ с максимумом кликов по этому запросу
    sq_rows = await db.execute(
        select(SearchQuery.query, SearchQuery.keyword_id,
               func.sum(SearchQuery.clicks).label("clicks"),
               func.sum(SearchQuery.impressions).label("impressions"))
        .where(SearchQuery.account_id == account_id, SearchQuery.keyword_id.isnot(None))
        .group_by(SearchQuery.query, SearchQuery.keyword_id)
    )
    sq_best: dict = {}
    for query_text, kw_id, clicks, impressions in sq_rows.all():
        key = normalize(query_text)
        if not key:
            continue
        weight = (int(clicks or 0), int(impressions or 0))
        if key not in sq_best or weight > sq_best[key][1]:
            sq_best[key] = (kw_id, weight)
    m.query_to_kw = {k: v[0] for k, v in sq_best.items()}

    # имя кампании → id, два уровня строгости
    camp_rows = await db.execute(
        select(Campaign.id, Campaign.name).where(Campaign.account_id == account_id)
    )
    for cid, name in camp_rows.all():
        m.campaign_exact.setdefault(normalize(name), cid)
        loose = normalize_campaign(name)
        if loose:
            m.campaign_loose[loose] = None if loose in m.campaign_loose else cid

    return m


def resolve_campaign_id(m: Matchers, *candidates) -> Optional[int]:
    names = [c for c in candidates if c]
    for name in names:
        cid = m.campaign_exact.get(normalize(name))
        if cid:
            return cid
    for name in names:
        cid = m.campaign_loose.get(normalize_campaign(name))
        if cid:
            return cid
    return None


def resolve_ad_group_id(m: Matchers, campaign_id: Optional[int],
                        group_name: Optional[str]) -> Optional[int]:
    """Группа ищется ТОЛЬКО внутри известной кампании — имя группы уникально
    лишь в пределах кампании."""
    if campaign_id is None or not group_name:
        return None
    ag = m.adgroup_exact.get((campaign_id, normalize(group_name)))
    if ag:
        return ag
    return m.adgroup_loose.get((campaign_id, normalize_campaign(group_name)))


def attribute(
    m: Matchers,
    *,
    ad_id: Optional[str] = None,
    term: Optional[str] = None,
    campaign_name: Optional[str] = None,
    chain_campaign: Optional[str] = None,
    chain_ad_group: Optional[str] = None,
) -> tuple[Optional[int], Optional[int], Optional[int], Optional[str]]:
    """Возвращает (keyword_id, ad_group_id, campaign_id, matched_by).

    Иерархия из цепочки: кампания (по имени, авторитетно) → группа (внутри
    кампании) → ключ (ad_id/запрос/фраза, внутри группы если известна).
    """
    # 1) КАМПАНИЯ — из цепочки/utm, имя приоритетно
    campaign_id = resolve_campaign_id(m, chain_campaign, campaign_name)

    # 2) ГРУППА — по имени внутри кампании
    ad_group_id = resolve_ad_group_id(m, campaign_id, chain_ad_group)

    # 3) КЛЮЧ — ad_id → запрос → фраза
    keyword_id = None
    matched_by = None
    norm_term = normalize(term) if term else None

    if ad_id and str(ad_id) in m.ad_id_to_kw:
        keyword_id = m.ad_id_to_kw[str(ad_id)]
        matched_by = "ad_id"
    elif norm_term and norm_term in m.query_to_kw:
        keyword_id = m.query_to_kw[norm_term]
        matched_by = "search_query"
    elif norm_term and norm_term in m.phrase_to_kw:
        keyword_id = m.phrase_to_kw[norm_term]
        matched_by = "phrase"

    if keyword_id is not None:
        # Кампания из цепочки не нашлась (переименовали) — берём из ключа.
        if campaign_id is None:
            campaign_id = m.kw_to_campaign.get(keyword_id)
        kw_campaign = m.kw_to_campaign.get(keyword_id)
        # Ключ уточняет группу, но только если он из ТОЙ ЖЕ кампании, что и
        # цепочка. Иначе доверяем цепочке (ключ мог сматчиться в другую
        # кампанию по одинаковому запросу).
        if ad_group_id is None and (campaign_id is None or kw_campaign == campaign_id):
            ad_group_id = m.kw_to_adgroup.get(keyword_id)
    else:
        # Ключа нет (типично для РСЯ) — уровень определяется группой/кампанией.
        if ad_group_id is not None:
            matched_by = "ad_group"
        elif campaign_id is not None:
            matched_by = "campaign"

    # Группа известна, а кампания нет — восстановим кампанию из группы.
    if campaign_id is None and ad_group_id is not None:
        campaign_id = m.adgroup_campaign.get(ad_group_id)

    return keyword_id, ad_group_id, campaign_id, matched_by


# ─── Пересчёт атрибуции для уже импортированных заявок ─────

async def reattribute_account(db: AsyncSession, account_id: int,
                              only_unmatched: bool = False,
                              commit: bool = True) -> dict:
    """Прогоняет каскад заново по всем заявкам аккаунта по актуальным данным
    Директа. Вызывать после синхронизации и перед ИИ-анализом."""
    m = await build_matchers(db, account_id)

    q = select(Lead).where(Lead.account_id == account_id)
    if only_unmatched:
        q = q.where(Lead.keyword_id.is_(None), Lead.campaign_id.is_(None))
    leads = (await db.execute(q)).scalars().all()

    stats = {
        "total": len(leads),
        "changed": 0,
        "gained_keyword": 0,
        "gained_ad_group": 0,
        "gained_campaign": 0,
        "lost_keyword": 0,
        "still_unmatched": 0,
        "by_match_method": {},
        "matchers": m.stats(),
    }

    for lead in leads:
        parsed = parse_source_chain(lead.source_raw)
        ad_id = lead.matched_ad_id or parsed.get("ad_id")
        term = lead.utm_term or parsed.get("term")

        kw_id, ag_id, camp_id, matched_by = attribute(
            m,
            ad_id=ad_id,
            term=term,
            campaign_name=lead.utm_campaign,
            chain_campaign=parsed.get("campaign"),
            chain_ad_group=parsed.get("ad_group"),
        )

        changed = (kw_id != lead.keyword_id or ag_id != lead.ad_group_id
                   or camp_id != lead.campaign_id or matched_by != lead.matched_by)
        if changed:
            if kw_id is not None and lead.keyword_id is None:
                stats["gained_keyword"] += 1
            if kw_id is None and lead.keyword_id is not None:
                stats["lost_keyword"] += 1
            if ag_id is not None and lead.ad_group_id is None:
                stats["gained_ad_group"] += 1
            if camp_id is not None and lead.campaign_id is None:
                stats["gained_campaign"] += 1
            lead.keyword_id = kw_id
            lead.ad_group_id = ag_id
            lead.campaign_id = camp_id
            lead.matched_by = matched_by
            if ad_id and not lead.matched_ad_id:
                lead.matched_ad_id = str(ad_id)
            stats["changed"] += 1

        key = matched_by or "unmatched"
        stats["by_match_method"][key] = stats["by_match_method"].get(key, 0) + 1
        if kw_id is None and ag_id is None and camp_id is None:
            stats["still_unmatched"] += 1

    if commit:
        await db.commit()
    logger.info(f"Reattribution account={account_id}: {stats}")
    return stats
