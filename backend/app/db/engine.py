"""Database engine switch — DuckDB (local) vs PostgreSQL (Azure).

Per Architecture Spec v3.6 §2.4 the API codebase is hosting-agnostic and the
local DuckDB development path is behaviourally identical to the deployed
PostgreSQL path. ``DB_ENGINE`` selects which driver is used; all higher layers
work against the abstract connection returned by :func:`connect`.

The driver module is imported lazily so that the local environment does not
require ``psycopg2`` and the cloud environment does not require ``duckdb``.
"""

from __future__ import annotations

import os
from typing import Any

from backend.app.config import settings


DUCKDB = "duckdb"
POSTGRESQL = "postgresql"


def engine_name() -> str:
    """Return the active engine identifier ('duckdb' or 'postgresql')."""
    name = settings.db_engine.strip().lower()
    if name not in (DUCKDB, POSTGRESQL):
        raise ValueError(f"Unsupported DB_ENGINE '{settings.db_engine}' (expected 'duckdb' or 'postgresql').")
    return name


def placeholder() -> str:
    """Return the parameter placeholder for the active engine.

    DuckDB uses qmark style (``?``); psycopg2 uses pyformat (``%s``). SQL is
    authored once and the placeholder substituted per engine so query logic is
    shared across both paths.
    """
    return "?" if engine_name() == DUCKDB else "%s"


def connect_duckdb(path: str) -> Any:
    """Open a DuckDB connection with the database attached as catalog ``obsdb``.

    The local database file is ``obs.duckdb`` (Local Dev Spec v1.1), so opening
    it directly would name the catalog ``obs`` — colliding with the ``obs``
    schema and making ``obs.<table>`` ambiguous. Connecting in-memory and
    attaching the file under the distinct alias ``obsdb`` (then ``USE``-ing it)
    keeps all schema-qualified ``obs.<table>`` references unambiguous, both here
    and at query time.
    """
    import duckdb  # lazy: only needed on the local path

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = duckdb.connect()
    escaped = path.replace("'", "''")
    conn.execute(f"ATTACH '{escaped}' AS obsdb")
    conn.execute("USE obsdb")
    return conn


def connect() -> Any:
    """Open and return a new DB-API connection for the active engine.

    DuckDB returns a :class:`duckdb.DuckDBPyConnection`; PostgreSQL returns a
    :class:`psycopg2.connection`. Both satisfy the DB-API 2.0 surface used by
    the data access helpers.
    """
    if engine_name() == DUCKDB:
        return connect_duckdb(settings.duckdb_path)

    import psycopg2  # lazy: only needed on the Azure path

    if not settings.postgresql_dsn:
        raise RuntimeError("DB_ENGINE=postgresql requires POSTGRESQL_DSN to be configured.")
    return psycopg2.connect(settings.postgresql_dsn)


def ping() -> bool:
    """Execute ``SELECT 1`` against the active engine; return True on success."""
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        return True
    finally:
        conn.close()
