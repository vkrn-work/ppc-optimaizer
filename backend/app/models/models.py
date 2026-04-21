"""
Схема БД PPC Optimizer
Все таблицы содержат account_id для мультикабинетности с первого дня.

v1.2 additions:
  - KeywordStat: weighted_impressions, weighted_ctr, bounce_rate, sessions
  - Campaign: epk_collapse_detected
  - HypothesisVerdict: neutral
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional
from sqlalchemy import (
    String, Integer, Numeric, Boolean, DateTime, Text, JSON,
    ForeignKey, UniqueConstraint, Index, Enum as SAEnum
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
import enum


class Base(DeclarativeBase):
    pass


# ─── Справочники ──────────────────────────────────────────────────────────────

class Account(Base):
    """Рекламный кабинет (один аккаунт Яндекс Директ)"""
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    yandex_login: Mapped[str] = mapped_column(String(255), unique=True)
    direct_client_id: Mapped[Optional[str]] = mapped_column(String(100))
    metrika_counter_id: Mapped[Optional[str]] = mapped_column(String(100))
    oauth_token: Mapped[Optional[str]] = mapped_column(Text)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    target_cpl: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    target_cpql: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    campaigns: Mapped[list["Campaign"]] = relationship(back_populates="account")
    rules: Mapped[list["Rule"]] = relationship(back_populates="account")


class Campaign(Base):
    """Рекламная кампания"""
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    direct_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(500))
    campaign_type: Mapped[str] = mapped_column(String(50))  # EPK / TGK / etc
    status: Mapped[str] = mapped_column(String(50))
    daily_budget: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    strategy: Mapped[Optional[str]] = mapped_column(String(100))
    strategy_type: Mapped[Optional[str]] = mapped_column(String(50))  # MANUAL_CPC / AUTO
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # v1.2: флаг ЕПК-обвала — выставляется аналитиком при обнаружении обвала
    epk_collapse_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("account_id", "direct_id"),)

    account: Mapped["Account"] = relationship(back_populates="campaigns")
    ad_groups: Mapped[list["AdGroup"]] = relationship(back_populates="campaign")
    stats: Mapped[list["CampaignStat"]] = relationship(back_populates="campaign")


class AdGroup(Base):
    """Группа объявлений"""
    __tablename__ = "ad_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    direct_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("account_id", "direct_id"),)

    campaign: Mapped["Campaign"] = relationship(back_populates="ad_groups")
    keywords: Mapped[list["Keyword"]] = relationship(back_populates="ad_group")


class Cluster(Base):
    """Кластер ключей (группировка по марке/стандарту материала)"""
    __tablename__ = "clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text)
    direction: Mapped[Optional[str]] = mapped_column(String(255))
    target_cpl: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    keywords: Mapped[list["Keyword"]] = relationship(back_populates="cluster")


class Keyword(Base):
    """Ключевое слово"""
    __tablename__ = "keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    ad_group_id: Mapped[int] = mapped_column(ForeignKey("ad_groups.id"), index=True)
    cluster_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clusters.id"), index=True)
    direct_id: Mapped[str] = mapped_column(String(100))
    phrase: Mapped[str] = mapped_column(Text)
    current_bid: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(50), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("account_id", "direct_id"),)

    ad_group: Mapped["AdGroup"] = relationship(back_populates="keywords")
    cluster: Mapped[Optional["Cluster"]] = relationship(back_populates="keywords")
    stats: Mapped[list["KeywordStat"]] = relationship(back_populates="keyword")


# ─── Статистика ───────────────────────────────────────────────────────────────

class CampaignStat(Base):
    """Статистика кампании по дням"""
    __tablename__ = "campaign_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    date: Mapped[datetime] = mapped_column(DateTime, index=True)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    spend: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    ctr: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    avg_cpc: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))

    __table_args__ = (UniqueConstraint("account_id", "campaign_id", "date"),)

    campaign: Mapped["Campaign"] = relationship(back_populates="stats")


class KeywordStat(Base):
    """
    Статистика ключа по дням.
    Поля из API Яндекс Директ (CRITERIA_PERFORMANCE_REPORT):
      - impressions, clicks, spend, ctr, avg_cpc     — базовые
      - avg_bid  (AvgEffectiveBid / 1_000_000)        — ставка с корректировками
      - avg_position (AvgImpressionPosition)           — позиция показа
      - avg_click_position (AvgClickPosition)          — позиция клика
      - traffic_volume (AvgTrafficVolume)              — объём трафика 0–150
      - weighted_impressions (WeightedImpressions)     — взвешенные показы  [v1.2]
      - weighted_ctr (WeightedCtr)                     — взвешенный CTR     [v1.2]
      - bounce_rate (BounceRate из Директа)            — отказы по клику    [v1.2]
    Поля из Яндекс Метрики (обогащение по utm_term):
      - sessions                                       — визиты (ym:s:visits) [v1.2]
    """
    __tablename__ = "keyword_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    keyword_id: Mapped[int] = mapped_column(ForeignKey("keywords.id"), index=True)
    date: Mapped[datetime] = mapped_column(DateTime, index=True)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    spend: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)

    # ── Базовые трафиковые метрики ────────────────────────────────────────────
    ctr: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))           # %
    avg_cpc: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))      # ₽

    # ── Ставка и аукцион ──────────────────────────────────────────────────────
    avg_bid: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))      # AvgEffectiveBid в ₽

    # ── Позиционные метрики ───────────────────────────────────────────────────
    avg_position: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    avg_click_position: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))

    # ── Объём рынка ───────────────────────────────────────────────────────────
    traffic_volume: Mapped[Optional[int]] = mapped_column(Integer)           # AvgTrafficVolume 0–150
    weighted_impressions: Mapped[Optional[int]] = mapped_column(Integer)     # v1.2
    weighted_ctr: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))  # v1.2

    # ── Поведенческие (из Директа + Метрики) ─────────────────────────────────
    bounce_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))   # v1.2, %
    sessions: Mapped[Optional[int]] = mapped_column(Integer)                 # v1.2, из Метрики

    # ── Служебные ─────────────────────────────────────────────────────────────
    ad_id: Mapped[Optional[str]] = mapped_column(String(100))

    __table_args__ = (
        UniqueConstraint("account_id", "keyword_id", "date"),
        Index("ix_kw_stat_date_account", "account_id", "date"),
    )

    keyword: Mapped["Keyword"] = relationship(back_populates="stats")


# ─── Лиды и воронка ───────────────────────────────────────────────────────────

class LeadStatus(str, enum.Enum):
    lead     = "lead"
    sql      = "sql"
    proposal = "proposal"
    deal     = "deal"
    lost     = "lost"


class Lead(Base):
    """Заявка из CRM с атрибуцией (Level 2 — подключается через CSV/API 1С)"""
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[LeadStatus] = mapped_column(SAEnum(LeadStatus))
    keyword_id: Mapped[Optional[int]] = mapped_column(ForeignKey("keywords.id"))
    client_id: Mapped[Optional[str]] = mapped_column(String(255))
    utm_source: Mapped[Optional[str]] = mapped_column(String(255))
    utm_medium: Mapped[Optional[str]] = mapped_column(String(255))
    utm_campaign: Mapped[Optional[str]] = mapped_column(String(255))
    utm_term: Mapped[Optional[str]] = mapped_column(String(500))
    revenue: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ─── Аналитика ────────────────────────────────────────────────────────────────

class AnalysisResult(Base):
    """Результат еженедельного анализа по кабинету"""
    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime)
    period_end: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    summary: Mapped[Optional[dict]] = mapped_column(JSON)
    problems: Mapped[Optional[list]] = mapped_column(JSON)
    opportunities: Mapped[Optional[list]] = mapped_column(JSON)

    suggestions: Mapped[list["Suggestion"]] = relationship(back_populates="analysis")


class KeywordMetrics(Base):
    """Рассчитанные метрики по ключу за скользящее окно (CRM Level 2)"""
    __tablename__ = "keyword_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    keyword_id: Mapped[int] = mapped_column(ForeignKey("keywords.id"), index=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analysis_results.id"), index=True)
    period_days: Mapped[int] = mapped_column(Integer, default=28)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    spend: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    leads: Mapped[int] = mapped_column(Integer, default=0)
    sqls: Mapped[int] = mapped_column(Integer, default=0)
    cr_click_lead: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    cr_lead_sql: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    cpl: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    cpql: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    is_significant: Mapped[bool] = mapped_column(Boolean, default=False)
    recommended_bid: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    bid_source: Mapped[str] = mapped_column(String(50), default="cluster")


# ─── Правила и предложения ────────────────────────────────────────────────────

class MetrikaSnapshot(Base):
    """Снапшот данных из Метрики — все срезы за один сбор"""
    __tablename__ = "metrika_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    date: Mapped[datetime] = mapped_column(DateTime, index=True)
    data: Mapped[dict] = mapped_column(JSON)


class SearchQuery(Base):
    """Поисковый запрос — реальная фраза по которой показывалась реклама"""
    __tablename__ = "search_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    keyword_id: Mapped[Optional[int]] = mapped_column(ForeignKey("keywords.id"), index=True)
    date: Mapped[datetime] = mapped_column(DateTime, index=True)
    query: Mapped[str] = mapped_column(Text)
    keyword_phrase: Mapped[Optional[str]] = mapped_column(Text)
    match_type: Mapped[Optional[str]] = mapped_column(String(50))
    campaign_id: Mapped[Optional[int]] = mapped_column(ForeignKey("campaigns.id"))
    ad_group_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ad_groups.id"))
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    spend: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    ctr: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    avg_cpc: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    avg_position: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    avg_click_position: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    is_irrelevant: Mapped[bool] = mapped_column(Boolean, default=False)
    is_added_as_keyword: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        Index("ix_sq_account_date", "account_id", "date"),
        Index("ix_sq_query", "account_id", "query"),
    )


class Rule(Base):
    """База правил для генерации предложений. Хранится в БД — можно менять без деплоя."""
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[Optional[int]] = mapped_column(ForeignKey("accounts.id"))
    name: Mapped[str] = mapped_column(String(255))
    condition_type: Mapped[str] = mapped_column(String(100))
    min_clicks: Mapped[Optional[int]] = mapped_column(Integer)
    cr_min: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 4))
    cr_max: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 4))
    cpql_multiplier: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    action_type: Mapped[str] = mapped_column(String(100))
    action_params: Mapped[Optional[dict]] = mapped_column(JSON)
    priority: Mapped[str] = mapped_column(String(20), default="this_week")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[Optional[str]] = mapped_column(Text)

    account: Mapped[Optional["Account"]] = relationship(back_populates="rules")


class SuggestionStatus(str, enum.Enum):
    pending     = "pending"
    approved    = "approved"
    rejected    = "rejected"
    applied     = "applied"
    rolled_back = "rolled_back"


class HypothesisVerdict(str, enum.Enum):
    confirmed   = "confirmed"
    rejected    = "rejected"
    insufficient = "insufficient"
    neutral     = "neutral"   # v1.2


class Suggestion(Base):
    """Предложение по изменению (ставка, стратегия, минус-слова и т.д.)"""
    __tablename__ = "suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analysis_results.id"), index=True)
    rule_id: Mapped[Optional[int]] = mapped_column(ForeignKey("rules.id"))
    object_type: Mapped[str] = mapped_column(String(50))
    object_id: Mapped[int] = mapped_column(Integer)
    object_name: Mapped[str] = mapped_column(Text)
    change_type: Mapped[str] = mapped_column(String(100))
    value_before: Mapped[Optional[str]] = mapped_column(String(500))
    value_after: Mapped[Optional[str]] = mapped_column(String(500))
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    expected_effect: Mapped[Optional[str]] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(20), default="this_week")
    status: Mapped[SuggestionStatus] = mapped_column(SAEnum(SuggestionStatus), default=SuggestionStatus.pending)
    reject_reason: Mapped[Optional[str]] = mapped_column(Text)
    approved_by: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    analysis: Mapped["AnalysisResult"] = relationship(back_populates="suggestions")
    hypothesis: Mapped[Optional["Hypothesis"]] = relationship(back_populates="suggestion")


class Hypothesis(Base):
    """Гипотеза — трекинг результата после применения изменения"""
    __tablename__ = "hypotheses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    suggestion_id: Mapped[int] = mapped_column(ForeignKey("suggestions.id"), unique=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime)
    track_until: Mapped[datetime] = mapped_column(DateTime)
    metrics_before: Mapped[Optional[dict]] = mapped_column(JSON)
    metrics_after: Mapped[Optional[dict]] = mapped_column(JSON)
    delta_percent: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 2))
    verdict: Mapped[Optional[HypothesisVerdict]] = mapped_column(
        SAEnum(HypothesisVerdict), nullable=True
    )
    report: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Extra fields for manual hypotheses (no suggestion)
    description: Mapped[Optional[str]] = mapped_column(Text)
    change_description: Mapped[Optional[str]] = mapped_column(Text)
    forecast: Mapped[Optional[str]] = mapped_column(Text)
    object_type: Mapped[Optional[str]] = mapped_column(String(50))
    object_id: Mapped[Optional[int]] = mapped_column(Integer)
    source: Mapped[Optional[str]] = mapped_column(String(50))

    suggestion: Mapped["Suggestion"] = relationship(back_populates="hypothesis")
