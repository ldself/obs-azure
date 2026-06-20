"""Backend-for-Frontend (BFF) OIDC endpoints (Security Spec v1.4 §6).

The browser SPA cannot set an httpOnly cookie, so the backend acts as the OIDC
confidential client: it runs the Entra ID authorization-code exchange and owns
the refresh token in an httpOnly secure cookie, while the SPA holds only the
access token in memory. This satisfies the §6 token-storage policy that the
§4.1.3 endpoint table does not itself enumerate — a deliberate, spec-driven
addition (see the Phase 1 plan / decisions).

Flow (cloud, LOCAL_AUTH_BYPASS off):
  GET  /api/v1/auth/login     → 302 to the Entra authorize endpoint
  GET  /api/v1/auth/callback  → code exchange; set refresh cookie; 302 to the SPA
  POST /api/v1/auth/refresh   → rotate tokens from the cookie; return access token
  POST /api/v1/auth/logout    → clear cookie; USER_LOGOUT audit; return logout URL

Locally these short-circuit: the SPA runs with VITE_AUTH_ENABLED=false and the
backend with LOCAL_AUTH_BYPASS=true, so the OIDC round-trip is skipped entirely.
"""

from __future__ import annotations

import secrets
from urllib.parse import quote

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi import status
from fastapi.responses import RedirectResponse

from backend.app.auth.token import InvalidTokenError
from backend.app.auth.token import validate_access_token
from backend.app.config import settings
from backend.app.db import helpers
from backend.app.services import audit_service


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite="lax",
        path="/api/v1/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name, path="/api/v1/auth"
    )


@router.get("/login")
def login(request: Request) -> RedirectResponse:
    """Begin the OIDC login by redirecting to Entra (or the SPA locally)."""
    if settings.local_auth_bypass:
        return RedirectResponse(url=settings.frontend_base_url)
    state = secrets.token_urlsafe(16)
    redirect_uri = settings.entra_redirect_uri
    if not redirect_uri:
        forwarded_host = request.headers.get("x-forwarded-host", "").split(",")[0].strip()
        if forwarded_host:
            redirect_uri = f"https://{forwarded_host}/api/v1/auth/callback"
        else:
            redirect_uri = f"{str(request.base_url).rstrip('/')}/api/v1/auth/callback"
    params = (
        f"client_id={settings.entra_client_id}"
        "&response_type=code"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        "&response_mode=query"
        f"&scope=openid profile email offline_access {settings.entra_client_id}/.default"
        f"&state={state}"
    )
    return RedirectResponse(url=f"{settings.entra_authorize_url}?{params}")


@router.get("/callback")
def callback(code: str | None = None) -> RedirectResponse:  # pragma: no cover - cloud-only
    """Exchange the authorization code, set the refresh cookie, redirect to the SPA."""
    if settings.local_auth_bypass:
        return RedirectResponse(url=settings.frontend_base_url)
    if not code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing code.")

    import httpx  # lazy: only needed on the cloud auth path

    token_response = httpx.post(
        settings.entra_token_url,
        data={
            "client_id": settings.entra_client_id,
            "client_secret": settings.entra_client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.entra_redirect_uri,
            "scope": f"openid profile email offline_access {settings.entra_client_id}/.default",
        },
    )
    if token_response.status_code != 200:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token exchange failed.")
    tokens = token_response.json()

    # Resolve the user and record the login event (AC-AUDIT-04).
    oid = ""
    try:
        oid = validate_access_token(tokens["access_token"]).get("oid", "")
    except (InvalidTokenError, KeyError):
        oid = ""
    if oid:
        with helpers.transaction() as conn:
            audit_service.write_audit_event(
                conn,
                event_type=audit_service.AuditEvent.USER_LOGIN,
                user_id=oid,
                entity_type="user",
                entity_id=oid,
            )

    redirect = RedirectResponse(url=settings.frontend_base_url)
    _set_refresh_cookie(redirect, tokens.get("refresh_token", ""))
    return redirect


@router.post("/refresh")
def refresh(request: Request, response: Response) -> dict[str, object]:
    """Mint a fresh access token from the refresh cookie (silent refresh, §6)."""
    if settings.local_auth_bypass:
        # No tokens are used locally; the SPA authenticates via the bypass path.
        return {"access_token": "", "expires_in": 0}

    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No refresh token.")

    import httpx  # lazy: only needed on the cloud auth path  # pragma: no cover

    token_response = httpx.post(  # pragma: no cover - cloud-only
        settings.entra_token_url,
        data={
            "client_id": settings.entra_client_id,
            "client_secret": settings.entra_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": f"openid profile email offline_access {settings.entra_client_id}/.default",
        },
    )
    if token_response.status_code != 200:  # pragma: no cover - cloud-only
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh failed.")
    tokens = token_response.json()  # pragma: no cover - cloud-only
    _set_refresh_cookie(response, tokens.get("refresh_token", refresh_token))  # pragma: no cover
    return {  # pragma: no cover - cloud-only
        "access_token": tokens.get("access_token", ""),
        "expires_in": tokens.get("expires_in", 0),
    }


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, str]:
    """Revoke the session: clear the refresh cookie and audit USER_LOGOUT (§6)."""
    oid = ""
    if settings.local_auth_bypass:
        oid = settings.local_auth_user_id
    else:  # pragma: no cover - cloud-only
        token = request.headers.get("authorization", "").partition(" ")[2]
        try:
            oid = validate_access_token(token).get("oid", "")
        except InvalidTokenError:
            oid = ""

    if oid:
        with helpers.transaction() as conn:
            audit_service.write_audit_event(
                conn,
                event_type=audit_service.AuditEvent.USER_LOGOUT,
                user_id=oid,
                entity_type="user",
                entity_id=oid,
            )

    _clear_refresh_cookie(response)
    logout_url = settings.frontend_base_url if settings.local_auth_bypass else settings.entra_logout_url
    return {"logout_url": logout_url}
