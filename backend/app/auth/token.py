"""Microsoft Entra ID access-token validation (Security Spec v1.4 §9.1 Step 1).

This is the cloud authentication path. It validates the JWT signature against the
tenant JWKS, and checks expiry, audience, and issuer. It is exercised only when
``LOCAL_AUTH_BYPASS`` is disabled; locally the mock path in
:mod:`backend.app.auth.dependencies` short-circuits before any of this runs.

Token validation failures raise :class:`InvalidTokenError`, which the request
pipeline translates into HTTP 401 (never 403 — an unauthenticated caller never
reaches the authorization steps).
"""

from __future__ import annotations

import threading
import time
from typing import Any

import jwt
from jwt import PyJWKClient

from backend.app.config import settings


class InvalidTokenError(Exception):
    """Raised when an access token is missing, malformed, expired, or untrusted."""


# A single JWKS client per process, refreshed by PyJWT's own key cache. Guarded by
# a lock so concurrent first-use does not create duplicate clients.
_jwks_client: PyJWKClient | None = None
_jwks_lock = threading.Lock()
_jwks_built_at: float = 0.0
# Rebuild the client occasionally so signing-key rotation is picked up.
_JWKS_TTL_SECONDS = 3600


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client, _jwks_built_at
    with _jwks_lock:
        expired = (time.time() - _jwks_built_at) > _JWKS_TTL_SECONDS
        if _jwks_client is None or expired:
            _jwks_client = PyJWKClient(settings.entra_jwks_uri)
            _jwks_built_at = time.time()
        return _jwks_client


def validate_access_token(token: str) -> dict[str, Any]:
    """Validate an Entra ID access token and return its claims.

    Verifies signature (RS256 against the tenant JWKS), ``exp``, ``aud`` (the OBS
    API client id), and ``iss`` (the tenant issuer in v1.0 or v2.0 format). Raises
    :class:`InvalidTokenError` on any failure.
    """
    if not token:
        raise InvalidTokenError("No access token presented.")
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        claims: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.entra_client_id,
            issuer=None,
            options={"require": ["exp", "aud", "iss"], "verify_iss": False},
        )
        _verify_entra_issuer(claims.get("iss", ""))
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
    except InvalidTokenError:
        raise
    return claims


def _verify_entra_issuer(issuer: str) -> None:
    """Verify that the issuer is from the configured Entra tenant (v1.0 or v2.0 format)."""
    tenant_id = settings.entra_tenant_id
    valid_issuers = {
        f"https://sts.windows.net/{tenant_id}/",
        f"https://login.microsoftonline.com/{tenant_id}/",
        f"https://login.microsoftonline.com/{tenant_id}/v2.0",
    }
    if issuer not in valid_issuers:
        raise InvalidTokenError(f"Invalid issuer: {issuer}")
