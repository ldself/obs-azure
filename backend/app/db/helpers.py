"""Shared data-access helpers built on the engine switch.

These thin helpers keep query logic identical across the DuckDB and PostgreSQL
paths (Architecture Spec v3.6 §2.4). Business logic and the 7-step authorization
flow are NOT implemented here — they arrive with their owning phases.
"""

from __future__ import annotations

from typing import Any
from typing import Sequence

from backend.app.db import engine


def fetch_all(sql: str, params: Sequence[Any] | None = None) -> list[tuple[Any, ...]]:
    """Run a read query and return all rows as tuples."""
    conn = engine.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, tuple(params or ()))
        return list(cur.fetchall())
    finally:
        conn.close()


def fetch_one(sql: str, params: Sequence[Any] | None = None) -> tuple[Any, ...] | None:
    """Run a read query and return the first row, or None."""
    conn = engine.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, tuple(params or ()))
        return cur.fetchone()
    finally:
        conn.close()
