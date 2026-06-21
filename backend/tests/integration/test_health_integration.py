"""Integration smoke test against a running API instance.

Integration tests run against the live local API (Local Dev Spec v1.1 §8.6):
start it with ``make api`` in another terminal, then ``make test-integration``.
If the API is not reachable the test is skipped rather than failed, so the
suite is meaningful both with and without a running server.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request

import pytest


API_BASE_URL = os.environ.get("OBS_API_BASE_URL", "http://localhost:8000")


def test_health_endpoint_live() -> None:
    """GET /api/health on the running API returns HTTP 200 (Build Plan §4.0.4)."""
    try:
        with urllib.request.urlopen(f"{API_BASE_URL}/api/health", timeout=2) as resp:
            assert resp.status == 200
    except urllib.error.URLError as exc:
        pytest.skip(f"API not running at {API_BASE_URL} ({exc}); start it with `make api`.")
