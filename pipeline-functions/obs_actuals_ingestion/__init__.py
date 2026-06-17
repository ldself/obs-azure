"""Azure Function entry point — actuals ingestion (hourly timer trigger).

Stub logic only (Build Sequencing Plan v1.2 §4.0.2): delegates to the
host-agnostic pipeline in backend/pipeline/actuals_pipeline.py, which is a no-op
in Phase 0. Full ingestion is implemented in Phase 2.
"""

from __future__ import annotations

import logging

import azure.functions as func

import _pipeline_bootstrap  # noqa: F401  (sys.path shim; app root is on sys.path)
from backend.pipeline import actuals_pipeline


def main(timer: func.TimerRequest) -> None:
    logging.info("obs_actuals_ingestion triggered (past_due=%s)", getattr(timer, "past_due", None))
    actuals_pipeline.run()
