#!/bin/bash

#
# 04_local_test_loop.sh
# Runs the automatic local loop: make test-unit and make test-integration (DuckDB + mock auth).
# Iterates until both pass with zero failures.
#
# Usage:
#   ./04_local_test_loop.sh [max_iterations]
#
# Default max_iterations: 1 (run once). Set to higher value to keep retrying on failure.
# Example: ./04_local_test_loop.sh 5    # Try up to 5 times
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
MAX_ITERATIONS="${1:-1}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
ITERATION=0
UNIT_PASSED=false
INTEGRATION_PASSED=false

echo -e "${BLUE}================================${NC}"
echo -e "${BLUE}Local Test Loop (DuckDB + Mock Auth)${NC}"
echo -e "${BLUE}================================${NC}"
echo "Repository root: $REPO_ROOT"
echo "Max iterations: $MAX_ITERATIONS"
echo ""

# Change to repo root
cd "$REPO_ROOT"

# Function to run unit tests
run_unit_tests() {
    echo -e "${YELLOW}[Iteration $1] Running: make test-unit${NC}"
    if make test-unit; then
        echo -e "${GREEN}✓ make test-unit PASSED${NC}"
        return 0
    else
        echo -e "${RED}✗ make test-unit FAILED${NC}"
        return 1
    fi
}

# Function to run integration tests
run_integration_tests() {
    echo -e "${YELLOW}[Iteration $1] Running: make test-integration${NC}"
    if make test-integration; then
        echo -e "${GREEN}✓ make test-integration PASSED${NC}"
        return 0
    else
        echo -e "${RED}✗ make test-integration FAILED${NC}"
        return 1
    fi
}

# Main loop
while [ $ITERATION -lt $MAX_ITERATIONS ]; do
    ITERATION=$((ITERATION + 1))
    echo -e "${BLUE}--- Iteration $ITERATION/$MAX_ITERATIONS ---${NC}"

    UNIT_PASSED=false
    INTEGRATION_PASSED=false

    # Run unit tests
    if run_unit_tests "$ITERATION"; then
        UNIT_PASSED=true
    fi

    echo ""

    # Run integration tests
    if run_integration_tests "$ITERATION"; then
        INTEGRATION_PASSED=true
    fi

    echo ""

    # Check if both passed
    if [ "$UNIT_PASSED" = true ] && [ "$INTEGRATION_PASSED" = true ]; then
        echo -e "${GREEN}================================${NC}"
        echo -e "${GREEN}SUCCESS: All tests passed!${NC}"
        echo -e "${GREEN}================================${NC}"
        echo "Iteration: $ITERATION"
        exit 0
    fi

    # If we haven't reached max iterations yet, continue
    if [ $ITERATION -lt $MAX_ITERATIONS ]; then
        echo -e "${YELLOW}Waiting before next iteration...${NC}"
        sleep 2
        echo ""
    fi
done

# If we get here, tests failed
echo -e "${RED}================================${NC}"
echo -e "${RED}FAILURE: Tests did not pass after $MAX_ITERATIONS iteration(s)${NC}"
echo -e "${RED}================================${NC}"
echo ""
echo "Summary:"
echo "  Unit tests: $([ "$UNIT_PASSED" = true ] && echo -e "${GREEN}PASSED${NC}" || echo -e "${RED}FAILED${NC}")"
echo "  Integration tests: $([ "$INTEGRATION_PASSED" = true ] && echo -e "${GREEN}PASSED${NC}" || echo -e "${RED}FAILED${NC}")"
echo ""
exit 1
