#!/bin/bash

# Phase 3 Validation: Automatic Test Suite Loop (DuckDB + mock auth)
# ===================================================================
# Runs `make test-unit` and `make test-integration` repeatedly until
# both exit with zero failures. The integration tests use FastAPI's
# TestClient and manage their own DuckDB databases via monkeypatch —
# no separately running API server is required.
#
# Usage:
#   ./03_test_suite_loop.sh            # up to MAX_ITERATIONS=20
#   MAX_ITERATIONS=5 ./03_test_suite_loop.sh
#   PAUSE_SECONDS=10 ./03_test_suite_loop.sh
#
# Exit codes:
#   0 — both suites passed with zero failures
#   1 — exhausted MAX_ITERATIONS without a clean run
#   2 — fatal setup error (venv or project root not found)

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && pwd)"
MAX_ITERATIONS="${MAX_ITERATIONS:-20}"
PAUSE_SECONDS="${PAUSE_SECONDS:-5}"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# ── Locate Python ────────────────────────────────────────────────────────────
if [ -f "$PROJECT_ROOT/.venv/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
else
    echo -e "${RED}FATAL: No Python found. Run 'python3 -m venv .venv && .venv/bin/pip install -r backend/requirements-dev.txt' from the project root.${NC}" >&2
    exit 2
fi

echo "========================================================================"
echo "Phase 3: Automatic Test Suite Loop"
echo "========================================================================"
echo "Project root : $PROJECT_ROOT"
echo "Python       : $PYTHON"
echo "Max iterations: $MAX_ITERATIONS   Pause between retries: ${PAUSE_SECONDS}s"
echo "Press Ctrl+C to abort."
echo ""

iteration=0
unit_exit=0
integ_exit=0

while [ "$iteration" -lt "$MAX_ITERATIONS" ]; do
    iteration=$((iteration + 1))

    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${CYAN}Iteration $iteration / $MAX_ITERATIONS — $(date '+%H:%M:%S')${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

    # ── Unit tests ─────────────────────────────────────────────────────────
    echo -e "\n${YELLOW}▶ make test-unit${NC}"
    (cd "$PROJECT_ROOT" && DB_ENGINE=duckdb LOCAL_AUTH_BYPASS=true make test-unit PYTHON="$PYTHON")
    unit_exit=$?

    if [ "$unit_exit" -eq 0 ]; then
        echo -e "${GREEN}✓ test-unit passed${NC}"
    else
        echo -e "${RED}✗ test-unit failed (exit $unit_exit)${NC}"
    fi

    # ── Integration tests ──────────────────────────────────────────────────
    echo -e "\n${YELLOW}▶ make test-integration${NC}"
    (cd "$PROJECT_ROOT" && DB_ENGINE=duckdb LOCAL_AUTH_BYPASS=true make test-integration PYTHON="$PYTHON")
    integ_exit=$?

    if [ "$integ_exit" -eq 0 ]; then
        echo -e "${GREEN}✓ test-integration passed${NC}"
    else
        echo -e "${RED}✗ test-integration failed (exit $integ_exit)${NC}"
    fi

    # ── Both clean? ────────────────────────────────────────────────────────
    echo ""
    if [ "$unit_exit" -eq 0 ] && [ "$integ_exit" -eq 0 ]; then
        echo "========================================================================"
        echo -e "${GREEN}All tests passed on iteration $iteration. Done.${NC}"
        echo "========================================================================"
        exit 0
    fi

    # ── Report what failed and pause before next attempt ──────────────────
    echo -e "${RED}Iteration $iteration incomplete:${NC}"
    [ "$unit_exit"  -ne 0 ] && echo -e "  ${RED}✗ test-unit${NC}"
    [ "$integ_exit" -ne 0 ] && echo -e "  ${RED}✗ test-integration${NC}"

    if [ "$iteration" -lt "$MAX_ITERATIONS" ]; then
        echo -e "Retrying in ${PAUSE_SECONDS}s…"
        sleep "$PAUSE_SECONDS"
    fi
done

echo ""
echo "========================================================================"
echo -e "${RED}Exhausted $MAX_ITERATIONS iterations without a clean run.${NC}"
[ "$unit_exit"  -ne 0 ] && echo -e "  ${RED}✗ test-unit still failing${NC}"
[ "$integ_exit" -ne 0 ] && echo -e "  ${RED}✗ test-integration still failing${NC}"
echo "========================================================================"
exit 1
