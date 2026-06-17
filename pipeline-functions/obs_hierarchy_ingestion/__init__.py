"""Azure Function entry point — hierarchy ingestion (Blob Storage event trigger).

Fires on blob-created events in the landing-hierarchies container for ad hoc
cost_center_hierarchy_* and account_hierarchy_* files. Stub logic only (Build
Sequencing Plan v1.2 §4.0.2): delegates to the host-agnostic pipeline in
backend/pipeline/hierarchy_pipeline.py, which is a no-op in Phase 0. Full
ingestion is implemented in Phase 2.
"""

from __future__ import annotations

import logging

import azure.functions as func

import _pipeline_bootstrap  # noqa: F401  (sys.path shim; app root is on sys.path)
from backend.pipeline import hierarchy_pipeline


def main(inputBlob: func.InputStream) -> None:  # noqa: N803 (Azure binding name)
    logging.info("obs_hierarchy_ingestion triggered for blob: %s (%s bytes)", inputBlob.name, inputBlob.length)
    hierarchy_pipeline.run(file_path=inputBlob.name)
