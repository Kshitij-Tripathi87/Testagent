from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings. Secrets are supplied only through the environment."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_name: str = "Testagent API"
    database_url: str = "sqlite:///./var/testagent.db"
    redis_url: str = "redis://localhost:6379/0"
    upload_dir: Path = Path("var/uploads")
    max_upload_mb: int = 25
    max_csv_rows: int = 2_000_000
    max_lineage_hops: int = 5
    app_encryption_key: str | None = None
    supabase_jwks_url: str | None = None
    supabase_issuer: str | None = None
    supabase_audience: str = "authenticated"
    dev_auth_enabled: bool = True
    cors_origins: str = "http://localhost:3000"
    datahub_allowed_hosts: str = ""
    datahub_writeback_enabled: bool = False
    auto_create_schema: bool = False

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def allowed_datahub_hosts(self) -> set[str]:
        return {host.strip().lower() for host in self.datahub_allowed_hosts.split(",") if host.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
