"""Shared data-access helpers built on the engine switch.

These thin helpers keep query logic identical across the DuckDB and PostgreSQL
paths (Architecture Spec v3.6 §2.4) so that all SQL is authored once against the
``obs.<table>`` schema names.

Engine note (DuckDB): the local connection attaches ``obs.duckdb`` as the catalog
``obsdb`` and ``USE``-s it (see :func:`backend.app.db.engine.connect_duckdb`).
That default-catalog selection is held on the *connection* and is NOT inherited
by a ``conn.cursor()``, whose default catalog falls back to ``memory`` — so a
cursor cannot resolve ``obs.<table>``. We therefore run DuckDB statements
directly on the connection (which keeps the ``obsdb`` catalog) and reserve
cursors for psycopg2. :func:`_runner` hides this difference; all reads and writes
go through it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from typing import Sequence

from backend.app.db import engine


def _runner(conn: Any, sql: str, params: Sequence[Any] | None) -> Any:
    """Execute ``sql`` on ``conn`` and return an object exposing ``fetch*``.

    DuckDB: execute on the connection itself so the attached ``obsdb`` catalog is
    used (a cursor would reset to the ``memory`` catalog). PostgreSQL: use a
    cursor, the standard DB-API surface for psycopg2.
    """
    if engine.engine_name() == engine.DUCKDB:
        return conn.execute(sql, list(params or ()))
    cur = conn.cursor()
    cur.execute(sql, tuple(params or ()))
    return cur


# --- Queries/writes on a caller-supplied connection (use inside transaction) -----
def query_all(conn: Any, sql: str, params: Sequence[Any] | None = None) -> list[tuple[Any, ...]]:
    """Run a read query on ``conn`` and return all rows."""
    return list(_runner(conn, sql, params).fetchall())


def query_one(conn: Any, sql: str, params: Sequence[Any] | None = None) -> tuple[Any, ...] | None:
    """Run a read query on ``conn`` and return the first row, or None."""
    return _runner(conn, sql, params).fetchone()


def exec_write(conn: Any, sql: str, params: Sequence[Any] | None = None) -> None:
    """Run a write statement on ``conn`` (no commit — the caller owns the txn)."""
    _runner(conn, sql, params)


# --- Standalone reads (own short-lived connection) -------------------------------
def fetch_all(sql: str, params: Sequence[Any] | None = None) -> list[tuple[Any, ...]]:
    """Run a read query in its own connection and return all rows as tuples."""
    conn = engine.connect()
    try:
        return query_all(conn, sql, params)
    finally:
        conn.close()


def fetch_one(sql: str, params: Sequence[Any] | None = None) -> tuple[Any, ...] | None:
    """Run a read query in its own connection and return the first row, or None."""
    conn = engine.connect()
    try:
        return query_one(conn, sql, params)
    finally:
        conn.close()


# --- Mutation helpers (Phase 1+) -------------------------------------------------
#
# DuckDB permits only one read-write connection to a database file at a time.
# Every helper here opens a connection and closes it in a ``finally`` block, so
# connections are never held concurrently. Within a single mutation, do ALL reads
# and writes on the one connection yielded by :func:`transaction` (via
# :func:`query_one`/:func:`exec_write`) — never call the standalone ``fetch_*``
# helpers inside an open transaction, as that would open a second connection to
# the same file.


def execute(sql: str, params: Sequence[Any] | None = None) -> None:
    """Run a single write statement in its own committed transaction.

    Use :func:`transaction` instead when a mutation and its audit write must be
    atomic (RULE 6).
    """
    with transaction() as conn:
        exec_write(conn, sql, params)


@contextmanager
def transaction() -> Iterator[Any]:
    """Yield a connection wrapped in a single atomic transaction.

    Commits on clean exit; rolls back on any exception and re-raises. This is the
    mechanism that makes a mutation and its synchronous audit-log write succeed or
    fail together (RULE 6): if the audit insert raises, the mutation is rolled
    back. DuckDB needs an explicit ``BEGIN`` to span multiple statements (each
    statement otherwise auto-commits); psycopg2 opens a transaction implicitly.
    """
    conn = engine.connect()
    try:
        if engine.engine_name() == engine.DUCKDB:
            conn.execute("BEGIN TRANSACTION")
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:  # pragma: no cover - rollback best-effort on a dead conn
            pass
        raise
    finally:
        conn.close()
