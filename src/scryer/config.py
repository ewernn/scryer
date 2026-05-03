"""scryer settings (env-driven via pydantic-settings).

Loaded once at process start; consumed by FastAPI app, Alembic env.py, and
service-layer code. All env reads happen here — no scattered os.environ access.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    env: Literal["dev", "staging", "prod"] = "dev"

    database_url: str = Field(
        default="postgresql+asyncpg://scryer:scryer@localhost:5432/scryer",
        description="Async SQLAlchemy URL. Neon Postgres in prod.",
    )
    # Pool sized for Neon -pooler (PgBouncer transaction mode), which
    # multiplexes many SQLAlchemy connections onto fewer server backends.
    # If DATABASE_URL points at a non-pooler host, drop these significantly.
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_pool_timeout: int = 30  # seconds before giving up on a free conn
    db_pool_recycle: int = 1800  # seconds; sidesteps Neon idle-disconnect

    r2_endpoint: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "scryer-blobs"

    encryption_key: str = Field(
        default="",
        description="AES-GCM key for Credentials encryption. Hex-encoded 32 bytes.",
    )
    encryption_key_version: int = 1

    jwt_secret: str = Field(
        default="",
        description="HMAC secret for JWT session tokens.",
    )
    jwt_ttl_seconds: int = 3600

    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
