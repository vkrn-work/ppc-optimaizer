"""
Схема БД PPC Optimizer
Все таблицы содержат account_id для мультикабинетности с первого дня.
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


# ─── Справочники ────────────────────────────────────────────────────────────

class Account(Base):
    """Рекламный кабинет (один аккаунт Яндекс Директ)"""
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    yandex_login: Mapped[str] = mapped_column(String(255), unique=True)
    direct_client_id: Mapped[Optional[str]] = mapped_column(String(100))
    metrika_counter_id: Mapped[Optional[str]] = mapped_column(String(100))
    oauth_token: Mapped[Optional[str]] = mapped_column(Text)  # зашифровать в проде
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
    campaign_type: Mapped[str] = mapped_column(String(50))  # EPK, TGK, etc.
    status: Mapped[str] = mapped_column(String(50))
    daily_budget: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    strategy: Mapped[Optional[str]] = mapped_column(String(100))
    strategy_type: Mapped[Optional[str]] = mapped_column(String(50))  # MANUAL_CPC / AUTO
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
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
    direction: Mapped[Optional[str]] = mapped_column(String(255))  # товарное направление
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


# ─── Статистика ──────────────────────────────────────────────────────────────

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
    """Статистика ключа по дням"""
    __tablename__ = "keyword_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    keyword_id: Mapped[int] = mapped_column(ForeignKey("keywords.id"), index=True)
    date: Mapped[datetime] = mapped_column(DateTime, index=True)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    spend: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    avg_cpc: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    avg_bid: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    avg_position: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    avg_click_position: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    traffic_volume: Mapped[Optional[int]] = mapped_column(Integer)
    ctr: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    ad_id: Mapped[Optional[str]] = mapped_column(String(100))

    __table_args__ = (
        UniqueConstraint("account_id", "keyword_id", "date"),
        Index("ix_kw_stat_date_account", "account_id", "date"),
    )

    keyword: Mapped["Keyword"] = relationship(back_populates="stats")


# ─── Лиды и воронка ──────────────────────────────────────────────────────────

class LeadStatus(str, enum.Enum):
    lead = "lead"
    sql = "sql"
    proposal = "proposal"
    deal = "deal"
    lost = "lost"


class Lead(Base):
    """Заявка из CRM с атрибуцией"""
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(255))  # ID в 1С
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    utm_source: Mapped[Optional[str]] = mapped_column(String(100))
    utm_campaign: Mapped[Optional[str]] = mapped_column(String(255))
    utm_term: Mapped[Optional[str]] = mapped_column(Text)
    client_id: Mapped[Optional[str]] = mapped_column(String(255))  # Метрика clientID
    roistat_id: Mapped[Optional[str]] = mapped_column(String(255))
    keyword_id: Mapped[Optional[int]] = mapped_column(ForeignKey("keywords.id"))
    status: Mapped[LeadStatus] = mapped_column(SAEnum(LeadStatus), default=LeadStatus.lead)
    status_updated: Mapped[Optional[datetime]] = mapped_column(DateTime)
    is_qualified: Mapped[bool] = mapped_column(Boolean, default=False)  # SQL
    is_bad: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        Index("ix_lead_utm_term", "account_id", "utm_term"),
        Index("ix_lead_client_id", "client_id"),
    )


# ─── Аналитика ───────────────────────────────────────────────────────────────

class AnalysisResult(Base):
    """Результат еженедельного анализа по кабинету"""
    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime)
    period_end: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    summary: Mapped[Optional[dict]] = mapped_column(JSON)  # сводные KPI
    problems: Mapped[Optional[list]] = mapped_column(JSON)  # топ-5 проблем
    opportunities: Mapped[Optional[list]] = mapped_column(JSON)  # точки роста

    suggestions: Mapped[list["Suggestion"]] = relationship(back_populates="analysis")


class KeywordMetrics(Base):
    """Рассчитанные метрики по ключу за скользящее окно"""
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
    bid_source: Mapped[str] = mapped_column(String(50), default="cluster")  # cluster/individual


# ─── Правила и предложения ───────────────────────────────────────────────────


class SearchQuery(Base):
    """Поисковый запрос — реальная фраза по которой показывалась реклама"""
    __tablename__ = "search_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    keyword_id: Mapped[Optional[int]] = mapped_column(ForeignKey("keywords.id"), index=True)
    date: Mapped[datetime] = mapped_column(DateTime, index=True)
    query: Mapped[str] = mapped_column(Text)  # реальный поисковый запрос
    keyword_phrase: Mapped[Optional[str]] = mapped_column(Text)  # ключ который сматчился
    match_type: Mapped[Optional[str]] = mapped_column(String(50))  # EXACT/PHRASE/BROAD
    campaign_id: Mapped[Optional[int]] = mapped_column(ForeignKey("campaigns.id"))
    ad_group_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ad_groups.id"))
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    spend: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    ctr: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    avg_cpc: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    avg_position: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    avg_click_position: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    # Флаги для анализа
    is_irrelevant: Mapped[bool] = mapped_column(Boolean, default=False)  # нерелевантный
    is_added_as_keyword: Mapped[bool] = mapped_column(Boolean, default=False)  # добавлен как ключ

    __table_args__ = (
        Index("ix_sq_account_date", "account_id", "date"),
        Index("ix_sq_query", "account_id", "query"),
    )


class Rule(Base):
    """
    База правил для генерации предложений.
    Хранится в БД — можно менять без деплоя.
    """
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[Optional[int]] = mapped_column(ForeignKey("accounts.id"))  # NULL = глобальное
    name: Mapped[str] = mapped_column(String(255))
    condition_type: Mapped[str] = mapped_column(String(100))
    # Параметры условия
    min_clicks: Mapped[Optional[int]] = mapped_column(Integer)
    cr_min: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 4))
    cr_max: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 4))
    cpql_multiplier: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    # Действие
    action_type: Mapped[str] = mapped_column(String(100))
    action_params: Mapped[Optional[dict]] = mapped_column(JSON)
    priority: Mapped[str] = mapped_column(String(20), default="this_week")  # today/this_week/month/scale
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[Optional[str]] = mapped_column(Text)

    account: Mapped[Optional["Account"]] = relationship(back_populates="rules")


class SuggestionStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    applied = "applied"
    rolled_back = "rolled_back"


class Suggestion(Base):
    """Предложение по изменению (ставка, стратегия, минус-слова и т.д.)"""
    __tablename__ = "suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analysis_results.id"), index=True)
    rule_id: Mapped[Optional[int]] = mapped_column(ForeignKey("rules.id"))
    object_type: Mapped[str] = mapped_column(String(50))  # keyword/cluster/campaign/ad_group
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
    track_until: Mapped[datetime] = mapped_column(DateTime)  # applied_at + 7 days
    metrics_before: Mapped[Optional[dict]] = mapped_column(JSON)
    metrics_after: Mapped[Optional[dict]] = mapped_column(JSON)
    delta_percent: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 2))
    verdict: Mapped[Optional[str]] = mapped_column(String(50))  # confirmed/rejected/insufficient
    report: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    suggestion: Mapped["Suggestion"] = relationship(back_populates="hypothesis")
