"""Apply the DuckDB schema bootstrap (``schema/bootstrap.sql``).

``make db-bootstrap`` runs this module. The bootstrap file contains many
``;``-separated statements; DuckDB's ``execute`` runs a single statement, so we
split and apply them in order. The DuckDB variant contains only
SCHEMA/TABLE/INDEX DDL (no function bodies), making a simple split safe — the
PostgreSQL variant with its ``$$`` trigger body is applied by psql in Azure,
not by this helper.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.app.config import settings


REPO_ROOT = Path(__file__).resolve().parents[3]
DUCKDB_BOOTSTRAP = REPO_ROOT / "schema" / "bootstrap.sql"


def _split_statements(sql: str) -> list[str]:
    """Split a DDL script into individual statements.

    Line comments (``--``) are stripped first so that a ``;`` appearing inside a
    comment never splits a statement. The DuckDB bootstrap contains no string
    literals with ``--``, so removing from the first ``--`` on each line is safe.
    """
    decommented_lines = [line.split("--", 1)[0] for line in sql.splitlines()]
    decommented = "\n".join(decommented_lines)
    statements = [stmt.strip() for stmt in decommented.split(";")]
    return [stmt for stmt in statements if stmt]


def apply_bootstrap(conn: Any, sql_path: Path = DUCKDB_BOOTSTRAP) -> int:
    """Execute every statement in ``sql_path`` against ``conn``; return the count."""
    sql = Path(sql_path).read_text(encoding="utf-8")
    statements = _split_statements(sql)
    for stmt in statements:
        conn.execute(stmt)
    return len(statements)


def bootstrap_duckdb(path: str | None = None) -> None:
    """Create/refresh the local DuckDB database from ``schema/bootstrap.sql``."""
    from backend.app.db.engine import connect_duckdb

    db_path = path or settings.duckdb_path
    conn = connect_duckdb(db_path)
    try:
        count = apply_bootstrap(conn)
        print(f"Applied {count} statements from {DUCKDB_BOOTSTRAP} to {db_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    bootstrap_duckdb()
