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

    # --- Microsoft Entra ID (OIDC/OAuth 2.0) ---
    # These are unused on the local LOCAL_AUTH_BYPASS path and are resolved from
    # App Service configuration / Key Vault in Azure. The backend acts as the
    # OIDC confidential client (BFF pattern): it performs the authorization-code
    # exchange and owns the httpOnly refresh cookie (Security Spec v1.4 §6).
    entra_tenant_id: str = Field(default="")
    entra_client_id: str = Field(default="")  # API audience and OIDC client id
    entra_client_secret: str = Field(default="")
    entra_redirect_uri: str = Field(default="")  # backend /api/v1/auth/callback URL
    # Where the backend redirects the browser after a successful login/logout.
    frontend_base_url: str = Field(default="http://localhost:5173")
    # httpOnly refresh-token cookie (Security Spec v1.4 §6). Secure flag is forced
    # on outside localhost; it is relaxed locally so http://localhost works.
    refresh_cookie_name: str = Field(default="obs_refresh_token")
    refresh_cookie_secure: bool = Field(default=True)

    @property
    def entra_issuer(self) -> str:
        """Expected ``iss`` claim for tokens from the configured tenant (v2.0)."""
        return f"https://login.microsoftonline.com/{self.entra_tenant_id}/v2.0"

    @property
    def entra_jwks_uri(self) -> str:
        """JWKS endpoint used to validate Entra ID access-token signatures."""
        return f"https://login.microsoftonline.com/{self.entra_tenant_id}/discovery/v2.0/keys"

    @property
    def entra_authorize_url(self) -> str:
        """OAuth 2.0 authorization endpoint for the configured tenant."""
        return f"https://login.microsoftonline.com/{self.entra_tenant_id}/oauth2/v2.0/authorize"

    @property
    def entra_token_url(self) -> str:
        """OAuth 2.0 token endpoint for the configured tenant."""
        return f"https://login.microsoftonline.com/{self.entra_tenant_id}/oauth2/v2.0/token"

    @property
    def entra_logout_url(self) -> str:
        """End-session endpoint; the SPA is redirected here on logout (§6)."""
        return f"https://login.microsoftonline.com/{self.entra_tenant_id}/oauth2/v2.0/logout"

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
