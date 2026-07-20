"""
v1.7.5: единый модуль атрибуции заявок. Вынесен из crm_importer.py.

Зачем отдельным модулем: атрибуция должна выполняться НЕ ТОЛЬКО в момент
импорта CRM-файла. На боевых данных главная причина «заявки не разносятся
по кампаниям/ключам» — не сам каскад матчинга, а порядок событий:

    выгрузку из CRM загружают раньше, чем досинхронизирован Директ
    (search_queries собираются отдельной задачей и приезжают позже)
    → в момент импорта query_to_kw пустой → keyword_id = NULL
    → и он остаётся NULL НАВСЕГДА, потому что пересчёта не было.

Теперь тот же самый код вызывается тремя путями:
  1. из crm_importer при импорте файла;
  2. из llm_context.build_context() перед каждым ИИ-анализом (дёшево, читает
     уже загруженные таблицы) — «мозг» гарантированно видит свежую разноску;
  3. вручную/по расписанию через POST /accounts/{id}/leads/reattribute.

Каскад (от точного к грубому):
  1. ad_id        — номер объявления из цепочки «Источник».
  2. search_query — utm_term (то, что ВВЁЛ пользователь) → search_queries → ключ.
  3. phrase       — точное совпадение utm_term с Keyword.phrase.
  4. campaign     — ключ не определён, но кампания известна.

ЧТО ИМЕННО ЧИНИТСЯ ОТНОСИТЕЛЬНО v1.7.4:

  A. campaign_id ВСЕГДА выводится из ключа, если ключ найден.
     Раньше campaign_id брался ТОЛЬКО из совпадения utm_campaign с именем
     кампании. Не совпало имя (лишний пробел, переименование кампании,
     колонка называется «Рекламная кампания» и не распозналась) — лид с
     найденным keyword_id получал campaign_id=NULL и ВЫПАДАЛ из разреза
     campaigns в llm_context. Отсюда кампании с реальными заявками,
     выглядящие нулевыми.

  B. Ключ по поисковому запросу выбирается по МАКСИМУМУ кликов, а не
     setdefault'ом. Один запрос обычно показывается по нескольким ключам;
     раньше побеждал произвольный (первый пришедший из БД) — заявка
     приписывалась случайному ключу. Ровно это и видно в отчётах как
     «заявки разнесены не по тем ключам».

  C. Матчинг имени кампании — двухуровневый: точный, затем «мягкий»
     (без домена/региона/номеров). При коллизии мягкого ключа не матчим
     вообще — лучше NULL, чем приписать не той кампании.

  D. Статусы SQL определяются по вхождению, а не по точному равенству.
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
    """Агрессивная нормализация ИМЕНИ КАМПАНИИ для запасного матчинга.

    Директологи кодируют в имени служебные хвосты:
        "Спецстали_Quard_все /gto365.ru /РФ3"
    а CRM отдаёт то же самое с другим разделителем, без домена или без
    региона. Отрезаем всё после первого '/', разделители → пробел,
    выкидываем голые числа.
    """
    t = normalize(s)
    if not t:
        return ""
    t = t.split("/")[0]
    t = re.sub(r"[_\-|.,()\[\]]+", " ", t)
    t = re.sub(r"\b\d+\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# ─── Классификация статусов воронки ────────────────────────

# Статус считается SQL, если ЛЮБОЙ маркер входит в текст. Именно вхождение,
# а не равенство: в реальных выгрузках статусы приходят с хвостами
# («КП отправлено», «Запущен БП №7», «Не прошло КП» — всё это SQL).
# Короткие маркеры (<=2 символа) ищутся как отдельные токены, чтобы не
# ловить случайные вхождения внутри слов.
SQL_MARKERS = (
    "кп", "бп", "заказ запущен", "сделка", "продажа", "выигран",
    "won", "deal", "proposal", "предложение", "счет", "договор",
)

JUNK_MARKERS = ("спам", "spam", "тест", "test", "фрод", "fraud", "дубл", "duplicate")


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

# Разные CRM/Роистат экспортируют цепочку с разными разделителями. Раньше
# сплит был только по '→' — выгрузка с другим разделителем давала
# campaign=None по ВСЕМ строкам, молча отключая запасной уровень атрибуции.
CHAIN_SEPARATORS = ("→", "->", "»", "|", " / ", " — ", " – ")


def parse_source_chain(raw: Optional[str]) -> dict:
    """кабинет → площадка → кампания → группа → id объявления → фраза."""
    result = {"cabinet": None, "platform": None, "campaign": None,
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

    for idx, key in enumerate(("cabinet", "platform", "campaign", "ad_group")):
        if len(parts) > idx:
            result[key] = parts[idx]

    if len(parts) >= 5:
        if parts[-1].isdigit():
            # РСЯ: цепочка заканчивается номером объявления, фразы нет
            result["ad_id"] = parts[-1]
        elif parts[-2].isdigit():
            # Поиск: ... → id объявления → фраза
            result["ad_id"] = parts[-2]
            result["term"] = parts[-1]
        else:
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

    def stats(self) -> dict:
        """Диагностика: если здесь нули, матчинг физически не на чем построить.
        Показывается в ответе импорта и пересчёта — чтобы «0 сматченных» не
        путали с «данных в Директе ещё нет»."""
        return {
            "ad_ids": len(self.ad_id_to_kw),
            "search_queries": len(self.query_to_kw),
            "phrases": len(self.phrase_to_kw),
            "campaigns": len(self.campaign_exact),
            "keywords_with_campaign": len(self.kw_to_campaign),
        }


async def build_matchers(db: AsyncSession, account_id: int) -> Matchers:
    m = Matchers()

    # ── keyword → campaign. Это и есть фикс (A): campaign_id больше не
    #    зависит от совпадения текстового имени кампании.
    kw_camp_rows = await db.execute(
        select(Keyword.id, Keyword.phrase, Campaign.id.label("campaign_id"))
        .join(AdGroup, Keyword.ad_group_id == AdGroup.id)
        .join(Campaign, AdGroup.campaign_id == Campaign.id)
        .where(Keyword.account_id == account_id)
    )
    for kw_id, phrase, camp_id in kw_camp_rows.all():
        m.kw_to_campaign[kw_id] = camp_id
        m.phrase_to_kw.setdefault(normalize(phrase), kw_id)

    # Ключи без группы/кампании тоже должны матчиться по фразе.
    orphan_rows = await db.execute(
        select(Keyword.id, Keyword.phrase).where(Keyword.account_id == account_id)
    )
    for kw_id, phrase in orphan_rows.all():
        m.phrase_to_kw.setdefault(normalize(phrase), kw_id)

    # ── ad_id → keyword: побеждает объявление с наибольшим числом кликов.
    ad_rows = await db.execute(
        select(
            KeywordStat.ad_id,
            KeywordStat.keyword_id,
            func.sum(KeywordStat.clicks).label("clicks"),
        )
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

    # ── поисковый запрос → keyword (фикс B): ключ с максимумом кликов по
    #    этому запросу, при равенстве — с максимумом показов.
    sq_rows = await db.execute(
        select(
            SearchQuery.query,
            SearchQuery.keyword_id,
            func.sum(SearchQuery.clicks).label("clicks"),
            func.sum(SearchQuery.impressions).label("impressions"),
        )
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

    # ── имя кампании → id, два уровня строгости (фикс C).
    camp_rows = await db.execute(
        select(Campaign.id, Campaign.name).where(Campaign.account_id == account_id)
    )
    for cid, name in camp_rows.all():
        m.campaign_exact.setdefault(normalize(name), cid)
        loose = normalize_campaign(name)
        if loose:
            # коллизия мягкого ключа → не матчим вообще: лучше NULL, чем
            # приписать заявку не той кампании
            m.campaign_loose[loose] = None if loose in m.campaign_loose else cid

    return m


def resolve_campaign_id(m: Matchers, *candidates) -> Optional[int]:
    """Кампания по нескольким текстовым кандидатам: сначала точно, потом мягко."""
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


def attribute(
    m: Matchers,
    *,
    ad_id: Optional[str] = None,
    term: Optional[str] = None,
    campaign_name: Optional[str] = None,
    chain_campaign: Optional[str] = None,
) -> tuple[Optional[int], Optional[int], Optional[str]]:
    """(keyword_id, campaign_id, matched_by). Каскад ad_id → search_query →
    phrase → campaign. campaign_id ВСЕГДА заполняется, если ключ найден."""
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

    campaign_id = resolve_campaign_id(m, campaign_name, chain_campaign)

    if keyword_id is not None:
        # Кампания ключа — источник истины. Имя из CRM может не совпасть
        # (переименование, лишние пробелы, нераспознанная колонка), но если
        # ключ найден, его кампания известна точно.
        kw_campaign = m.kw_to_campaign.get(keyword_id)
        if kw_campaign is not None:
            campaign_id = kw_campaign
    elif campaign_id is not None:
        matched_by = "campaign"

    return keyword_id, campaign_id, matched_by


# ─── Пересчёт атрибуции для уже импортированных заявок ─────

async def reattribute_account(db: AsyncSession, account_id: int,
                              only_unmatched: bool = False,
                              commit: bool = True) -> dict:
    """Прогоняет каскад заново по всем заявкам аккаунта.

    Вызывать после каждой синхронизации Директа и перед ИИ-анализом: заявки,
    импортированные до того, как приехали search_queries, иначе так и
    остаются с keyword_id=NULL. Это и есть основная причина «в отчётах
    заявки не разнесены».

    only_unmatched=True — трогать только те, у кого нет ни ключа, ни кампании
    (безопасный режим, ничего уже сматченного не перезапишет).
    """
    m = await build_matchers(db, account_id)

    q = select(Lead).where(Lead.account_id == account_id)
    if only_unmatched:
        q = q.where(Lead.keyword_id.is_(None), Lead.campaign_id.is_(None))
    leads = (await db.execute(q)).scalars().all()

    stats = {
        "total": len(leads),
        "changed": 0,
        "gained_keyword": 0,
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

        kw_id, camp_id, matched_by = attribute(
            m,
            ad_id=ad_id,
            term=term,
            campaign_name=lead.utm_campaign,
            chain_campaign=parsed.get("campaign"),
        )

        if kw_id != lead.keyword_id or camp_id != lead.campaign_id or matched_by != lead.matched_by:
            if kw_id is not None and lead.keyword_id is None:
                stats["gained_keyword"] += 1
            if kw_id is None and lead.keyword_id is not None:
                stats["lost_keyword"] += 1
            if camp_id is not None and lead.campaign_id is None:
                stats["gained_campaign"] += 1
            lead.keyword_id = kw_id
            lead.campaign_id = camp_id
            lead.matched_by = matched_by
            if ad_id and not lead.matched_ad_id:
                lead.matched_ad_id = str(ad_id)
            stats["changed"] += 1

        key = matched_by or "unmatched"
        stats["by_match_method"][key] = stats["by_match_method"].get(key, 0) + 1
        if kw_id is None and camp_id is None:
            stats["still_unmatched"] += 1

    if commit:
        await db.commit()
    logger.info(f"Reattribution account={account_id}: {stats}")
    return stats
