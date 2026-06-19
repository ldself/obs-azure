"""AC coverage gate (``make ac-coverage``).

Reads ``backend/tests/ac_registry.yaml`` and, for every acceptance criterion that
declares a ``test:`` node id, runs the mapped test. Exits non-zero if any
automated AC is missing a test mapping or if any mapped test fails. ACs marked
``verification: infra`` are reported as out-of-scope for automated coverage (they
are validated against Azure during the per-phase cloud validation loop).

Usage:
    python -m backend.tests.check_ac_coverage [phase_0 ...]
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml


REGISTRY = Path(__file__.replace("check_ac_coverage.py", "ac_registry.yaml"))


def main(argv: list[str]) -> int:
    data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8")) or {}
    phases = argv or list(data.keys())

    test_ids: list[str] = []
    infra: list[str] = []
    unmapped: list[str] = []

    for phase in phases:
        for ac in data.get(phase, []):
            ac_id = ac.get("id", "<unknown>")
            if ac.get("test"):
                test_ids.append(ac["test"])
            elif ac.get("verification") == "infra":
                infra.append(ac_id)
            else:
                unmapped.append(ac_id)

    if unmapped:
        print(f"AC coverage FAILED — unmapped acceptance criteria: {unmapped}")
        return 1

    if infra:
        print(f"Infra-validated ACs (not automated): {infra}")

    if not test_ids:
        print("No automated ACs to run for the selected phase(s).")
        return 0

    print(f"Running {len(test_ids)} AC-mapped test(s)...")
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", *test_ids])
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
