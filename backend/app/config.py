"""Application configuration and environment loading.

Settings are read once at startup from the process environment (populated from
``.env.local`` in local development via python-dotenv). The configuration layer
also enforces the RULE 8 ``LOCAL_AUTH_BYPASS`` safety constraint: the bypass is
permitted only on localhost, the value is read from the environment exactly once
at startup, and it cannot be set through any runtime mechanism (API call, header).
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


# Load .env.local for local development. In Azure the values come from App
# Service / Functions configuration and Key Vault references, so a missing file
# is not an error.
load_dotenv(".env.local")


class Settings(BaseSettings):
    """Typed view over the OBS runtime environment."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    # --- Database engine (Architecture Spec v3.6 §2.4) ---
    db_engine: str = Field(default="duckdb")  # 'duckdb' (local) | 'postgresql' (Azure)
    duckdb_path: str = Field(default="./local-data/obs.duckdb")
    # PostgreSQL connection is supplied as a full libpq DSN / connection string in
    # Azure (resolved from Key Vault). Empty locally where DuckDB is used.
    postgresql_dsn: str = Field(default="")

    # --- Local mock authentication (RULE 8: localhost only) ---
    local_auth_bypass: bool = Field(default=False)
    local_auth_user_id: str = Field(default="")
    local_auth_user_email: str = Field(default="")
    local_auth_user_name: str = Field(default="")

    # --- Filesystem landing zone (substitutes for Azure Blob Storage locally) ---
    landing_zone_path: str = Field(default="./local-data/landing-zone")
    archive_path: str = Field(default="./local-data/archive")
    error_path: str = Field(default="./local-data/error")

    # --- CORS / logging ---
    cors_allowed_origins: str = Field(default="http://localhost:5173")
    log_level: str = Field(default="INFO")

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS origins split from the comma-separated environment value."""
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


def _is_localhost() -> bool:
    """Return True only when the process is running on a developer's localhost.

    Azure App Service and Azure Functions both set ``WEBSITE_INSTANCE_ID`` in the
    runtime environment; its presence is a reliable signal that we are NOT on
    localhost. An explicit ``OBS_ENV`` of ``production`` / ``cloud`` also disqualifies.
    """
    if os.environ.get("WEBSITE_INSTANCE_ID"):
        return False
    if os.environ.get("OBS_ENV", "").lower() in {"production", "prod", "cloud", "azure"}:
        return False
    return True


def assert_local_auth_bypass_safe(settings: Settings) -> None:
    """RULE 8 startup assertion.

    Raises ``RuntimeError`` if ``LOCAL_AUTH_BYPASS`` is enabled while the process
    is not running on localhost. This is checked once at startup; the bypass can
    never be toggled at runtime because the value originates only from the
    environment read at process start.
    """
    if settings.local_auth_bypass and not _is_localhost():
        raise RuntimeError(
            "LOCAL_AUTH_BYPASS=true is only permitted on localhost (RULE 8). "
            "It must never be enabled in a deployed Azure environment."
        )


settings = Settings()
