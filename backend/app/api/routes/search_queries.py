"""Метрика-снапшоты и поисковые запросы.

Выделено из монолитного routes.py без изменения логики.
"""
from datetime import datetime
from typing import Optional
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.models import (
    Campaign, AdGroup, AnalysisResult, Suggestion, SuggestionStatus,
    MetrikaSnapshot,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ─── Metrika snapshot ───────────────────────

@router.get("/accounts/{account_id}/metrika-snapshot")
async def get_metrika_snapshot(account_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(MetrikaSnapshot)
        .where(MetrikaSnapshot.account_id == account_id)
        .order_by(desc(MetrikaSnapshot.date))
        .limit(2)
    )
    snapshots = result.scalars().all()
    if not snapshots:
        raise HTTPException(404, "No Metrika data yet")
    snap = snapshots[0]
    prev = snapshots[1] if len(snapshots) > 1 else None
    return {
        "date":      snap.date.isoformat(),
        "data":      snap.data,
        "prev_date": prev.date.isoformat() if prev else None,
        "prev_data": prev.data if prev else None,
    }


# ─── Search queries ───────────────────────

@router.get("/accounts/{account_id}/search-queries")
async def get_search_queries(
    account_id: int,
    suggest:    str = "",
    campaign_id: Optional[int] = None,
    ad_group_id: Optional[int] = None,
    search:     Optional[str]  = None,
    limit:      int = 200,
    db: AsyncSession = Depends(get_db),
):
    from app.models.models import SearchQuery
    from sqlalchemy import or_

    q = select(SearchQuery).where(SearchQuery.account_id == account_id)
    # CHANGED v1.7.0: campaign_id было объявлено в сигнатуре, но никогда не
    # применялось к запросу — фильтр по кампании молча игнорировался.
    if campaign_id:
        q = q.where(SearchQuery.campaign_id == campaign_id)
    if ad_group_id:
        q = q.where(SearchQuery.ad_group_id == ad_group_id)
    if search:
        q = q.where(SearchQuery.query.ilike(f"%{search}%"))

    NEGATIVE_SIGNALS   = ['стандарт','что такое','скачать','характеристики','описание','гост','нормативы','документ','pdf']
    COMMERCIAL_SIGNALS = ['купить','цена','поставка','заказ','прайс','производитель','поставщик','мм','дюйм','лист','труба','прокат','сталь','полоса','лента']

    if suggest == "negatives":
        neg_filters = [SearchQuery.query.ilike(f"%{s}%") for s in NEGATIVE_SIGNALS]
        q = q.where(
            SearchQuery.clicks >= 2,
            or_(*neg_filters) if neg_filters else True,
        ).order_by(SearchQuery.spend.desc())
    elif suggest == "new_keywords":
        q = q.where(
            SearchQuery.clicks >= 2,
            SearchQuery.match_type != 'EXACT',
        ).order_by(SearchQuery.clicks.desc())
    else:
        q = q.order_by(SearchQuery.clicks.desc())

    q = q.limit(limit)
    result = await db.execute(q)
    rows = result.scalars().all()

    def score_query(query_text):
        qt = (query_text or "").lower()
        score = 50
        for sig in COMMERCIAL_SIGNALS:
            if sig in qt: score += 10
        for sig in NEGATIVE_SIGNALS:
            if sig in qt: score -= 20
        import re
        if re.search(r'\d+[xх×]\d+|\d+мм|\d+"\s|\d/\d', qt): score += 20
        return max(0, min(100, score))

    return [{
        "id":                r.id,
        "query":             r.query,
        "keyword_phrase":    r.keyword_phrase,
        "match_type":        r.match_type,
        "clicks":            r.clicks,
        "impressions":       r.impressions,
        "spend":             float(r.spend) if r.spend else 0,
        "ctr":               round(float(r.ctr), 2) if r.ctr else None,
        "avg_position":      round(float(r.avg_position), 2) if r.avg_position else None,
        "avg_click_position": round(float(r.avg_click_position), 2) if r.avg_click_position else None,
        "commercial_score":  score_query(r.query),
        "date":              r.date.isoformat() if r.date else None,
    } for r in rows]


class SearchQueryActionRequest(BaseModel):
    action: str  # "add_negative" | "add_keyword"


@router.post("/accounts/{account_id}/search-queries/{query_id}/action")
async def action_search_query(
    account_id: int,
    query_id: int,
    data: SearchQueryActionRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    v1.7.0 — превращает конкретную поисковую фразу (уже собранную и
    оцененную commercial_score в GET /search-queries) в pending-предложение
    по тому же approve/apply-контуру, что и остальные Suggestion:
      - add_negative → change_type=add_negatives на группу объявлений, где
        встретилась фраза (SearchQuery.ad_group_id)
      - add_keyword  → change_type=add_keywords на ту же группу («золотая
        фраза» по терминологии PPC_Audit_Playbook — реальный поисковый запрос
        с явным коммерческим сигналом, которого ещё нет в семантике)
    Если в этой группе уже есть pending-предложение того же типа — новое
    слово ДОПИСЫВАЕТСЯ в существующее (а не создаёт дубль и не блокируется
    дедупликацией по (object_id, change_type), как было бы при наивной вставке.
    """
    from app.models.models import SearchQuery

    if data.action not in ("add_negative", "add_keyword"):
        raise HTTPException(400, "action должен быть 'add_negative' или 'add_keyword'")

    sq_res = await db.execute(
        select(SearchQuery).where(
            SearchQuery.id == query_id, SearchQuery.account_id == account_id
        )
    )
    sq = sq_res.scalar_one_or_none()
    if not sq:
        raise HTTPException(404, "Поисковый запрос не найден")
    if not sq.ad_group_id:
        raise HTTPException(400, "У этой фразы не определена группа объявлений — нельзя создать предложение")

    change_type = "add_negatives" if data.action == "add_negative" else "add_keywords"
    word = sq.query.strip()

    existing_q = await db.execute(
        select(Suggestion).where(and_(
            Suggestion.account_id == account_id,
            Suggestion.object_type == "ad_group",
            Suggestion.object_id == sq.ad_group_id,
            Suggestion.change_type == change_type,
            Suggestion.status == SuggestionStatus.pending,
        ))
    )
    existing = existing_q.scalar_one_or_none()

    ag_res = await db.execute(select(AdGroup).where(AdGroup.id == sq.ad_group_id))
    ag = ag_res.scalar_one_or_none()
    camp_name = ""
    if ag:
        camp_res = await db.execute(select(Campaign).where(Campaign.id == ag.campaign_id))
        camp = camp_res.scalar_one_or_none()
        camp_name = f"{camp.name} → " if camp else ""

    if existing:
        words = [w.strip() for w in (existing.value_after or "").split(",") if w.strip()]
        if word not in words:
            words.append(word)
        existing.value_after = ", ".join(words)
        existing.rationale = (existing.rationale or "") + f" | + «{word}» (поисковая фраза, commercial_score учтен на странице Анализ)"
        await db.commit()
        return {"status": "merged", "suggestion_id": existing.id, "value_after": existing.value_after}

    analysis = AnalysisResult(
        account_id=account_id,
        period_start=datetime.utcnow(),
        period_end=datetime.utcnow(),
        summary={"source": "search_query_action", "query": word, "action": data.action},
        problems=[],
    )
    db.add(analysis)
    await db.flush()

    s = Suggestion(
        account_id=account_id,
        analysis_id=analysis.id,
        object_type="ad_group",
        object_id=sq.ad_group_id,
        object_name=f"{camp_name}{ag.name if ag else ''}",
        change_type=change_type,
        value_before=None,
        value_after=word,
        rationale=(
            f"Поисковая фраза «{word}» (commercial_score-анализ, раздел «Анализ» → «Поисковые фразы»). "
            + ("Похоже на нецелевой/информационный трафик — в минус-слова." if data.action == "add_negative"
               else "Реальный конверсионный запрос без соответствующего ключа — «золотая фраза» по методологии аудита, добавить как точный ключ.")
        ),
        expected_effect=("Отсечь нерелевантный трафик по этому запросу" if data.action == "add_negative"
                        else "Захватить точный трафик по уже подтверждённому спросом запросу"),
        priority="this_week",
        status=SuggestionStatus.pending,
    )
    db.add(s)

    if data.action == "add_negative":
        sq.is_irrelevant = True
    else:
        sq.is_added_as_keyword = True

    await db.commit()
    await db.refresh(s)
    return {"status": "created", "suggestion_id": s.id, "change_type": change_type, "value_after": word}
