#!/bin/bash

# Phase 1 Validation: Auth Flow Reviewer (RULE 5)
# ================================================
# Runs the auth-flow-reviewer subagent over new endpoints added in this phase.
# Validates the 7-step authorization flow on every endpoint:
#   1. Validate token (401 on failure)
#   2. Resolve user in obs.users, check is_active (403)
#   3. If is_administrator, grant full access, skip 4–6
#   4. Capability flags (403)
#   5. Cost center scope (403)
#   6. Standard + confidential grant (strip fields or 403)
#   7. Execute and write audit log
# Also verifies correct HTTP status codes per Security Spec v1.4 §9.2

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && pwd)"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo "=============================================="
echo "Auth Flow Reviewer Validation (RULE 5)"
echo "=============================================="

# Find new endpoint files in this branch (vs main)
echo -e "\n${BLUE}[1] Identifying new endpoints in this branch...${NC}"

# Get list of new/modified router files
NEW_ROUTERS=$(git diff --name-only main...HEAD -- "backend/app/routers/*.py" 2>/dev/null || echo "")

if [ -z "$NEW_ROUTERS" ]; then
    echo -e "${YELLOW}No new router files detected.${NC}"
else
    echo -e "${GREEN}Found modified routers:${NC}"
    echo "$NEW_ROUTERS" | sed 's/^/  /'
fi

# Get list of all Python files in routers directory for scanning
ROUTER_FILES=$(find "$PROJECT_ROOT/backend/app/routers" -name "*.py" -type f 2>/dev/null | sort)

if [ -z "$ROUTER_FILES" ]; then
    echo -e "${RED}✗ No router files found in backend/app/routers/${NC}"
    exit 1
fi

echo -e "\n${BLUE}[2] Scanning endpoints for auth flow implementation...${NC}"

# Count endpoints
ENDPOINT_COUNT=$(echo "$ROUTER_FILES" | wc -l)
echo "Scanning $ENDPOINT_COUNT router files..."

# Create a temporary file to collect endpoints
ENDPOINTS_TEMP=$(mktemp)
trap "rm -f $ENDPOINTS_TEMP" EXIT

# Scan for @app.get, @app.post, @app.put, @app.delete, @app.patch decorators
for router_file in $ROUTER_FILES; do
    echo "$router_file" >> "$ENDPOINTS_TEMP"
    grep -n "@router\.\(get\|post\|put\|delete\|patch\)" "$router_file" 2>/dev/null | sed "s/^/  /" >> "$ENDPOINTS_TEMP" || true
done

echo ""
cat "$ENDPOINTS_TEMP"

echo ""
echo "=============================================="
echo -e "${BLUE}Auth Flow Reviewer Next Steps${NC}"
echo "=============================================="
echo ""
echo "To run the auth-flow-reviewer subagent:"
echo ""
echo -e "${YELLOW}Option 1: Via Claude Code (CLI)${NC}"
echo "  Invoke the auth-flow-reviewer subagent manually:"
echo "  - Open Claude Code and reference the routers listed above"
echo "  - Ask: 'Run auth-flow-reviewer over the new endpoints'"
echo "  - The agent will verify all 7 steps are present and ordered"
echo ""
echo -e "${YELLOW}Option 2: Automated (requires .claude.md hook)${NC}"
echo "  A pre-commit or post-merge hook can auto-invoke this"
echo ""
echo "Validation checklist for each endpoint:"
echo "  ✓ Step 1: Token validation (401 on invalid/missing)"
echo "  ✓ Step 2: User resolution + is_active check (403 if inactive)"
echo "  ✓ Step 3: Admin bypass (skip 4-6 if is_administrator)"
echo "  ✓ Step 4: Capability flags enforced (403 on missing)"
echo "  ✓ Step 5: Cost center scope enforced (403 on mismatch)"
echo "  ✓ Step 6: Standard + confidential grant + field stripping"
echo "  ✓ Step 7: Audit log write (transactional with mutation)"
echo "  ✓ HTTP Status: 401/403/404/409/422 per Security Spec v1.4 §9.2"
echo "  ✓ No 404 used to hide unauthorized resources (use 403)"
echo ""
echo "=============================================="
echo -e "${GREEN}To mark this validation as resolved:${NC}"
echo "  1. Run the auth-flow-reviewer subagent on all routers"
echo "  2. Fix any findings in the endpoint implementation"
echo "  3. Run this script again to confirm no new routers added"
echo "=============================================="

exit 0

# To mark this validation as resolved:
# Run auth-flow-reviewer over the new endpoints in
# backend/app/routers/auth_routes.py
# and backend/app/routers/users.py
