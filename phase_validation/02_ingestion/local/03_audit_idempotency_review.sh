#!/bin/bash

# Phase 2 Validation: Audit & Idempotency Review
# ====================================================================
# Invokes the audit-and-idempotency-reviewer subagent over all new
# pipeline endpoints and services to verify two non-negotiable invariants:
#
#   (1) AUDIT LOG (RULE 6): every create/update/delete writes an audit
#       event to obs.audit_log in the same transaction; if the audit
#       write fails the mutation fails.
#
#   (2) IDEMPOTENCY (RULE 10): every ingestion promotion uses
#       INSERT … ON CONFLICT DO UPDATE keyed on SHA-256 content hash
#       + file name; running the same file twice produces no duplicate rows.
#
# Exit codes:
#   0  — No findings; gate cleared.
#   1  — Findings exist or claude CLI unavailable; resolve before passing
#         the Phase 2 gate.

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && pwd)"

# ── colors ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}======================================================${NC}"
echo -e "${BLUE}Phase 2 — Audit & Idempotency Review${NC}"
echo -e "${BLUE}======================================================${NC}"
echo "Project root: $PROJECT_ROOT"
echo ""

# ── preflight: claude CLI required ───────────────────────────────────────────
if ! command -v claude &>/dev/null; then
    echo -e "${RED}ERROR: 'claude' CLI not found in PATH.${NC}"
    echo "Install Claude Code and ensure it is on PATH, then re-run."
    exit 1
fi

# ── build the review prompt ───────────────────────────────────────────────────
REVIEW_PROMPT="You are the audit-and-idempotency-reviewer subagent for the OBS project.

Scope — examine every file under these directories:
  backend/pipeline/
  backend/app/routers/
  backend/app/services/

For each function that writes to the database verify the following two invariants.

INVARIANT 1 — AUDIT LOG (RULE 6)
Every create, update, or delete must write an append-only record to obs.audit_log
within the SAME transaction as the mutation. If the audit write fails the mutation
must be rolled back. Flag any mutation that:
  - omits the obs.audit_log write entirely, OR
  - writes to obs.audit_log outside the mutation transaction (e.g. after commit), OR
  - swallows audit write errors instead of propagating them.

INVARIANT 2 — IDEMPOTENCY (RULE 10)
Every ingestion promotion must use INSERT … ON CONFLICT DO UPDATE (atomic upsert).
The deduplication key in obs.ingestion_control is SHA-256 content hash + file name.
Flag any promotion path that:
  - uses a bare INSERT (no ON CONFLICT clause), OR
  - performs a SELECT-then-INSERT pattern (race-prone), OR
  - does not compute or store the SHA-256 content hash before inserting.

Working directory for all file reads: $PROJECT_ROOT

Output format — numbered findings only, no prose preamble:
  <N>. <relative/file/path>:<line>  [RULE 6 | RULE 10]  <one-sentence description>

If there are no findings, output exactly this line and nothing else:
  AUDIT REVIEW PASSED: No findings.

If there are findings, list them and end with exactly:
  AUDIT REVIEW FAILED: <N> finding(s) require resolution."

# ── run the review ─────────────────────────────────────────────────────────────
echo -e "${YELLOW}Invoking audit-and-idempotency-reviewer...${NC}"
echo ""

OUTPUT=$(claude -p "$REVIEW_PROMPT" --allowedTools "Read,Bash" 2>&1)
CLAUDE_EXIT=$?

echo "$OUTPUT"
echo ""

# ── interpret results ──────────────────────────────────────────────────────────
if [ $CLAUDE_EXIT -ne 0 ]; then
    echo -e "${RED}======================================================${NC}"
    echo -e "${RED}claude CLI exited with code $CLAUDE_EXIT.${NC}"
    echo -e "${RED}Check credentials / connectivity and re-run.${NC}"
    echo -e "${RED}======================================================${NC}"
    exit 1
fi

if echo "$OUTPUT" | grep -qF "AUDIT REVIEW PASSED"; then
    echo -e "${GREEN}======================================================${NC}"
    echo -e "${GREEN}Audit & Idempotency Review: PASSED — no findings.${NC}"
    echo -e "${GREEN}======================================================${NC}"
    exit 0
else
    echo -e "${RED}======================================================${NC}"
    echo -e "${RED}Audit & Idempotency Review: FAILED${NC}"
    echo -e "${RED}Resolve all findings above before passing Phase 2 gate.${NC}"
    echo -e "${RED}======================================================${NC}"
    exit 1
fi
