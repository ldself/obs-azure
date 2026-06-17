"""Shared import shim for the Functions entry points.

The ingestion logic lives in ``backend/pipeline/`` (host-agnostic). The Functions
deployment package includes the repository so the entry points can import that
logic unchanged (Azure Cloud Migration Spec v2.0 §8.2). This shim ensures the
repo root is importable regardless of the Functions working directory.
"""

from __future__ import annotations

import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
