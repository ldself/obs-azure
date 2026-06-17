"""Azure Function entry point — employees ingestion (daily timer trigger, 02:00 UTC).

Stub logic only (Build Sequencing Plan v1.2 §4.0.2): delegates to the
host-agnostic pipeline in backend/pipeline/employees_pipeline.py, which is a
no-op in Phase 0. Full ingestion is implemented in Phase 2.
"""

from __future__ import annotations

import logging

import azure.functions as func

import _pipeline_bootstrap  # noqa: F401  (sys.path shim; app root is on sys.path)
from backend.pipeline import employees_pipeline


def main(timer: func.TimerRequest) -> None:
    logging.info("obs_employees_ingestion triggered (past_due=%s)", getattr(timer, "past_due", None))
    employees_pipeline.run()
