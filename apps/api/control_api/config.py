from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AITC_", env_file=".env", extra="ignore")

    env: str = "development"  # development | test | production
    database_url: str = "postgresql+psycopg://aitc:aitc@localhost:5432/aitc"
    # Development-only bearer tokens ("dev-human:<subject>"). Refused in production.
    dev_auth_enabled: bool = True
    cors_origins: list[str] = ["http://localhost:3000"]
    idempotency_retention_days: int = 30
    page_size_default: int = 50
    page_size_max: int = 100
    lease_ttl_seconds: int = 60
    max_active_tickets_per_project: int = 2
    # Read access to project repositories for local pilot setup (a fine-grained token is enough).
    github_token: str = Field(default="", validation_alias="GITHUB_TOKEN")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.env == "production" and s.dev_auth_enabled:
        raise RuntimeError("AITC_DEV_AUTH_ENABLED must be false in production")
    return s
