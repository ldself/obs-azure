"""Employees ingestion pipeline (stub).

Phase 0 stub. Full implementation (Phase 2): detect employees_* files, validate,
write valid rows to obs.employees_staging, quarantine referential-integrity
failures to obs.employees_quarantine, and promote idempotently to obs.employees
keyed on the natural key p_number + cost_center (RULE 10).
"""

from __future__ import annotations

import logging


logger = logging.getLogger("obs.pipeline.employees")


def run(file_path: str | None = None) -> None:
    """Entry point invoked by the Makefile and the Azure Function trigger.

    Stub only — performs no ingestion in Phase 0.
    """
    logger.info("employees_pipeline.run stub invoked (file_path=%s) — no-op in Phase 0", file_path)
