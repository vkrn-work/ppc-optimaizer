from pydantic_settings import BaseSettings
from typing import Optional
import os


def _fix_db_url(url: str) -> str:
    """Railway даёт postgres:// — asyncpg требует postgresql+asyncpg://"""
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://") and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://ppc:ppc_secret@db:5432/ppc_optimizer"
    REDIS_URL: str = "redis://redis:6379/0"

    def __init__(self, **data):
        super().__init__(**data)
        # Fix Railway's postgres:// format
        object.__setattr__(self, 'DATABASE_URL', _fix_db_url(self.DATABASE_URL))
    SECRET_KEY: str = "change-this-secret-key-in-production"
    ENVIRONMENT: str = "production"
    ALLOWED_ORIGINS: str = "http://localhost:3000"

    YANDEX_CLIENT_ID: Optional[str] = None
    YANDEX_CLIENT_SECRET: Optional[str] = None
    YANDEX_OAUTH_URL: str = "https://oauth.yandex.ru"
    YANDEX_DIRECT_API_URL: str = "https://api.direct.yandex.com/json/v5"
    YANDEX_METRIKA_API_URL: str = "https://api-metrika.yandex.net"

    # Пороги CR (из воркфлоу и плейбука)
    CR_HIGH_THRESHOLD: float = 0.15
    CR_MID_THRESHOLD: float = 0.05
    CR_LOW_THRESHOLD: float = 0.03
    CR_CRITICAL_THRESHOLD: float = 0.01
    MIN_CLICKS_KEYWORD: int = 30
    MIN_CLICKS_CAMPAIGN: int = 100
    ANALYSIS_WINDOW_DAYS: int = 28

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",")]

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
