#!/bin/bash

# Phase 1 Validation: LOCAL_AUTH_BYPASS Safety Constraint (RULE 8)
# ================================================================
# Validates that LOCAL_AUTH_BYPASS=true is permitted ONLY on localhost.
# Raises RuntimeError at startup if:
#   1. LOCAL_AUTH_BYPASS=true AND WEBSITE_INSTANCE_ID is set (Azure)
#   2. LOCAL_AUTH_BYPASS=true AND OBS_ENV is production/prod/cloud/azure

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && pwd)"
VENV_DIR="/tmp/obs_validation_venv"

# Setup venv
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR" >/dev/null 2>&1
    source "$VENV_DIR/bin/activate"
    pip install -q -r "$PROJECT_ROOT/backend/requirements.txt" 2>/dev/null
else
    source "$VENV_DIR/bin/activate"
fi

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass_count=0
fail_count=0
test_count=0

# Helper to run a single test
test_case() {
    local num=$1
    local name=$2
    local bypass=$3
    local website=$4
    local env=$5
    local should_fail=$6

    ((test_count++))
    echo -e "\n${YELLOW}[Test $test_count]${NC} $name"

    local script=$(mktemp)
    cat > "$script" << 'PYSCRIPT'
import sys, os
sys.path.insert(0, os.environ.get('PROJECT_ROOT', '.'))
try:
    from backend.app.main import app
    sys.exit(0)
except RuntimeError as e:
    if "LOCAL_AUTH_BYPASS=true is only permitted on localhost" in str(e):
        sys.exit(42)
    sys.exit(2)
PYSCRIPT

    local exit_code=0
    (
        export PROJECT_ROOT="$PROJECT_ROOT"
        export PYTHONPATH="$VENV_DIR/lib/python3.14/site-packages:$PROJECT_ROOT"
        [ -n "$bypass" ] && export LOCAL_AUTH_BYPASS="$bypass"
        [ -n "$website" ] && export WEBSITE_INSTANCE_ID="$website"
        [ -n "$env" ] && export OBS_ENV="$env"
        "$VENV_DIR/bin/python3" "$script"
    ) 2>/dev/null || exit_code=$?
    rm -f "$script"

    if [ "$should_fail" = "true" ]; then
        if [ "$exit_code" = "42" ]; then
            echo -e "${GREEN}✓ PASS${NC} – Assertion raised as expected"
            ((pass_count++))
        else
            echo -e "${RED}✗ FAIL${NC} – Expected assertion, got exit $exit_code"
            ((fail_count++))
        fi
    else
        if [ "$exit_code" = "0" ]; then
            echo -e "${GREEN}✓ PASS${NC} – Startup succeeded as expected"
            ((pass_count++))
        else
            echo -e "${RED}✗ FAIL${NC} – Expected success, got exit $exit_code"
            ((fail_count++))
        fi
    fi
}

echo "=============================================="
echo "LOCAL_AUTH_BYPASS Safety Constraint (RULE 8)"
echo "=============================================="

# Run tests: (num, name, LOCAL_AUTH_BYPASS, WEBSITE_INSTANCE_ID, OBS_ENV, should_fail)
test_case 1 "LOCAL_AUTH_BYPASS=true on localhost (default)" "true" "" "" "false"
test_case 2 "LOCAL_AUTH_BYPASS=true with WEBSITE_INSTANCE_ID (Azure)" "true" "app-001" "" "true"
test_case 3 "LOCAL_AUTH_BYPASS=true with OBS_ENV=production" "true" "" "production" "true"
test_case 4 "LOCAL_AUTH_BYPASS=true with OBS_ENV=prod" "true" "" "prod" "true"
test_case 5 "LOCAL_AUTH_BYPASS=true with OBS_ENV=cloud" "true" "" "cloud" "true"
test_case 6 "LOCAL_AUTH_BYPASS=true with OBS_ENV=azure" "true" "" "azure" "true"
test_case 7 "LOCAL_AUTH_BYPASS=false with WEBSITE_INSTANCE_ID" "false" "app-001" "" "false"
test_case 8 "LOCAL_AUTH_BYPASS=false on localhost" "false" "" "" "false"
test_case 9 "LOCAL_AUTH_BYPASS not set (defaults to false)" "" "" "" "false"

echo ""
echo "=============================================="
echo -e "Results: ${GREEN}$pass_count passed${NC}, ${RED}$fail_count failed${NC} (of $test_count tests)"
echo "=============================================="

[ $fail_count -eq 0 ] && echo -e "${GREEN}All tests passed!${NC}" && exit 0
echo -e "${RED}Some tests failed.${NC}" && exit 1
