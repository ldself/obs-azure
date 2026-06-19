"""Unit tests for Entra ID access-token validation (Security Spec v1.4 §9.1 Step 1).

The JWKS network fetch and JWT signature check are mocked — this exercises our
validation wrapper and error mapping, not PyJWT internals (Local Dev Spec v1.1
§10.1: token validation path with token validation mocked).
"""

from __future__ import annotations

import jwt
import pytest

from backend.app.auth import token as token_module
from backend.app.auth.token import InvalidTokenError
from backend.app.auth.token import validate_access_token


class _FakeKey:
    key = "fake-public-key"


class _FakeJwksClient:
    def get_signing_key_from_jwt(self, _token: str) -> _FakeKey:
        return _FakeKey()


@pytest.fixture(autouse=True)
def _reset_jwks(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the JWKS client to our fake and reset the module cache.
    monkeypatch.setattr(token_module, "_jwks_client", None)
    monkeypatch.setattr(token_module, "_jwks_built_at", 0.0)
    monkeypatch.setattr(token_module, "PyJWKClient", lambda _uri: _FakeJwksClient())


def test_empty_token_raises() -> None:
    with pytest.raises(InvalidTokenError):
        validate_access_token("")


def test_valid_token_returns_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    claims = {"oid": "abc-123", "email": "u@example.com", "name": "User"}
    monkeypatch.setattr(jwt, "decode", lambda *a, **k: claims)
    result = validate_access_token("header.payload.sig")
    assert result["oid"] == "abc-123"


def test_invalid_signature_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> dict[str, object]:
        raise jwt.InvalidSignatureError("bad signature")

    monkeypatch.setattr(jwt, "decode", _boom)
    with pytest.raises(InvalidTokenError):
        validate_access_token("header.payload.sig")


def test_expired_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _expired(*_a: object, **_k: object) -> dict[str, object]:
        raise jwt.ExpiredSignatureError("expired")

    monkeypatch.setattr(jwt, "decode", _expired)
    with pytest.raises(InvalidTokenError):
        validate_access_token("header.payload.sig")


def test_jwks_client_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jwt, "decode", lambda *a, **k: {"oid": "x"})
    validate_access_token("a.b.c")
    first = token_module._jwks_client
    validate_access_token("a.b.c")
    assert token_module._jwks_client is first
