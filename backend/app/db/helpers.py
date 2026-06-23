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


def fetch_all_with_cols(
    sql: str, params: Sequence[Any] | None = None
) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Run a read query and return (column_names, rows). Used for SELECT * queries
    where callers need to map results dynamically (e.g. quarantine row_data)."""
    conn = engine.connect()
    try:
        result = _runner(conn, sql, params)
        rows = list(result.fetchall())
        col_names = [d[0] for d in result.description]
        return col_names, rows
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


# --- Idempotent upsert helpers (RULE 10) -----------------------------------------


def merge(
    conn: Any,
    table: str,
    key_cols: list[str],
    data: dict[str, Any],
    immutable_cols: list[str] | None = None,
) -> None:
    """Idempotent upsert of one record into ``table`` on the natural key ``key_cols``.

    Routes to :func:`_pg_upsert` (atomic INSERT … ON CONFLICT DO UPDATE) on
    PostgreSQL, or :func:`_duckdb_upsert` (DELETE + INSERT) on DuckDB — the
    approved local substitute per Implementation Guide v1.2 §5.4.

    ``data`` must contain values for ALL columns being written, including the key
    columns. ``key_cols`` must correspond to an existing UNIQUE index on ``table``.

    ``immutable_cols`` lists columns that must only be written on first INSERT and
    must never be overwritten by an UPDATE (e.g. ``employee_id``, ``created_at``).
    On PostgreSQL the DO UPDATE SET clause omits these columns. On DuckDB (≥ 0.10)
    the same ON CONFLICT DO UPDATE syntax is used; the caller pre-fetches the
    correct immutable column values and passes them in ``data``, so the DO UPDATE
    SET clause includes them but writes the same value (idempotent).

    Every pipeline promotion must call merge() — never a bare INSERT (RULE 10).
    The exception is obs.actuals, which uses a soft-delete pattern (see
    actuals_pipeline.py) and relies on file-level SHA-256 dedup for idempotency.
    """
    if engine.engine_name() == engine.DUCKDB:
        _duckdb_upsert(conn, table, key_cols, data)
    else:
        _pg_upsert(conn, table, key_cols, data, immutable_cols or [])


def _pg_upsert(
    conn: Any,
    table: str,
    key_cols: list[str],
    data: dict[str, Any],
    immutable_cols: list[str],
) -> None:
    """PostgreSQL-specific atomic upsert via INSERT … ON CONFLICT DO UPDATE."""
    ph = engine.placeholder()
    all_cols = list(data.keys())
    values = [data[c] for c in all_cols]

    conflict_target = ", ".join(key_cols)
    col_list = ", ".join(all_cols)
    placeholders = ", ".join(ph for _ in all_cols)

    update_cols = [c for c in all_cols if c not in key_cols and c not in immutable_cols]
    if update_cols:
        set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        do_update = f"DO UPDATE SET {set_clause}"
    else:
        do_update = "DO NOTHING"

    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict_target}) {do_update}"
    )
    exec_write(conn, sql, values)


def _duckdb_upsert(
    conn: Any,
    table: str,
    key_cols: list[str],
    data: dict[str, Any],
) -> None:
    """DuckDB-specific upsert via INSERT … ON CONFLICT DO UPDATE (DuckDB ≥ 0.10).

    DuckDB 0.10+ supports ON CONFLICT DO UPDATE on UNIQUE INDEXes, identical to
    PostgreSQL syntax. The caller pre-fetches immutable column values (employee_id,
    created_at) and passes them in ``data``, so the DO UPDATE SET clause can safely
    include them (writing the same value is idempotent).
    """
    ph = engine.placeholder()
    all_cols = list(data.keys())
    values = [data[c] for c in all_cols]

    conflict_target = ", ".join(key_cols)
    col_list = ", ".join(all_cols)
    placeholders = ", ".join(ph for _ in all_cols)

    update_cols = [c for c in all_cols if c not in key_cols]
    if update_cols:
        set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        do_update = f"DO UPDATE SET {set_clause}"
    else:
        do_update = "DO NOTHING"

    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict_target}) {do_update}"
    )
    exec_write(conn, sql, values)
