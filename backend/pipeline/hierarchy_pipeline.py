"""Hierarchy ingestion pipeline (stub).

Phase 0 stub. Full implementation (Phase 2): triggered ad hoc by a Blob-created
event for cost_center_hierarchy_* and account_hierarchy_* files; validate and
promote idempotently into the hierarchy node/membership tables, quarantining
records whose dimensions are not yet resolved (RULE 10).
"""

from __future__ import annotations

import logging


logger = logging.getLogger("obs.pipeline.hierarchy")


def run(file_path: str | None = None) -> None:
    """Entry point invoked by the Makefile and the Azure Function trigger.

    Stub only — performs no ingestion in Phase 0.
    """
    logger.info("hierarchy_pipeline.run stub invoked (file_path=%s) — no-op in Phase 0", file_path)
