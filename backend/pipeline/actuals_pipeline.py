"""Actuals ingestion pipeline (stub).

Phase 0 stub. Full implementation (Phase 2): detect actuals_* files in the
landing zone, validate, write valid rows to obs.actuals_staging, quarantine
referential-integrity failures, and promote idempotently to obs.actuals via
INSERT ... ON CONFLICT DO UPDATE keyed on the SHA-256 content hash + file name
in obs.ingestion_control (RULE 10).
"""

from __future__ import annotations

import logging


logger = logging.getLogger("obs.pipeline.actuals")


def run(file_path: str | None = None) -> None:
    """Entry point invoked by the Makefile and the Azure Function trigger.

    Stub only — performs no ingestion in Phase 0.
    """
    logger.info("actuals_pipeline.run stub invoked (file_path=%s) — no-op in Phase 0", file_path)
