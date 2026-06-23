#!/bin/bash

# Phase 2 Validation: Automatic Local Test Suite Loop
# =====================================================
# Runs `make test-unit` and `make test-integration` repeatedly until both
# pass with zero failures, or until MAX_ATTEMPTS is reached.
#
# Prerequisites:
#   - DuckDB backend available (DB_ENGINE=duckdb, no running API required for
#     unit tests; integration tests spin up their own fixtures via conftest).
#   - LOCAL_AUTH_BYPASS=true (localhost only — checked by startup assertion).
#   - Virtualenv at /tmp/obs_validation_venv or system Python with deps installed.
#
# Exit codes:
#   0  both suites passed with zero failures
#   1  MAX_ATTEMPTS exhausted before both suites passed

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && pwd)"
MAX_ATTEMPTS=10

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}======================================================${NC}"
echo -e "${BLUE}Phase 2 — Automatic Local Test Suite Loop${NC}"
echo -e "${BLUE}======================================================${NC}"
echo "Project root : $PROJECT_ROOT"
echo "Max attempts : $MAX_ATTEMPTS"
echo ""

export DB_ENGINE="duckdb"
export LOCAL_AUTH_BYPASS="true"

attempt=0
unit_passed=false
integration_passed=false

while [[ $attempt -lt $MAX_ATTEMPTS ]]; do
    attempt=$(( attempt + 1 ))
    echo -e "${YELLOW}──── Attempt ${attempt} / ${MAX_ATTEMPTS} ────────────────────────────────${NC}"

    # ── make test-unit ────────────────────────────────────────────────────────
    echo -e "${BLUE}[unit]${NC} Running make test-unit ..."
    if make -C "$PROJECT_ROOT" test-unit 2>&1; then
        echo -e "${GREEN}[unit] PASSED${NC}"
        unit_passed=true
    else
        echo -e "${RED}[unit] FAILED${NC}"
        unit_passed=false
    fi

    # ── make test-integration ─────────────────────────────────────────────────
    echo -e "${BLUE}[integration]${NC} Running make test-integration ..."
    if make -C "$PROJECT_ROOT" test-integration 2>&1; then
        echo -e "${GREEN}[integration] PASSED${NC}"
        integration_passed=true
    else
        echo -e "${RED}[integration] FAILED${NC}"
        integration_passed=false
    fi

    # ── both passed? ──────────────────────────────────────────────────────────
    if $unit_passed && $integration_passed; then
        echo ""
        echo -e "${GREEN}======================================================${NC}"
        echo -e "${GREEN}Both suites passed with zero failures (attempt ${attempt}).${NC}"
        echo -e "${GREEN}======================================================${NC}"
        exit 0
    fi

    if [[ $attempt -lt $MAX_ATTEMPTS ]]; then
        echo -e "${YELLOW}One or more suites failed. Retrying...${NC}"
        echo ""
    fi
done

echo ""
echo -e "${RED}======================================================${NC}"
echo -e "${RED}Max attempts (${MAX_ATTEMPTS}) reached. Suites still failing.${NC}"
echo -e "${RED}  unit        : $( $unit_passed        && echo PASSED || echo FAILED )${NC}"
echo -e "${RED}  integration : $( $integration_passed && echo PASSED || echo FAILED )${NC}"
echo -e "${RED}======================================================${NC}"
exit 1
