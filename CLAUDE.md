# OPEX Budgeting System (OBS) — Claude Code Orientation

This is the primary in-repo entry point for Claude Code. Read it in full at the start of
every session before writing any code. It summarizes and points to the authoritative
specifications; it does not replace them. **All decisions are final.** Do not re-litigate
architecture, terminology, or design. If a specification appears to contain an error or
contradiction, **STOP and raise it as a question** — do not make a unilateral change
(see RULE 2).

---

## Session Startup Protocol

Run at the start of **every** session, before any code is written:

1. Search project knowledge for "Project Context Handoff" and read the current version
   summary to orient to project state.
2. Identify which Build Sequencing Plan phase is currently in progress.
3. Search project knowledge for the specific specification sections relevant to the task
   at hand before writing any code.
4. Confirm the local environment is running (`make api`, `npm run dev`) before attempting
   any integration test.
5. Never assume a decision is unmade. Search project knowledge first.

---

## The 10 Absolute Rules

These are non-negotiable and apply to every line of code, every API contract, and every
database schema element.

**RULE 1 — Terminology is exact.** Every identifier in code (variables, functions,
columns, enum values, API path segments, UI labels) uses the exact term from the System
Context and Domain Glossary v1.11. No synonyms. No abbreviating terms not already
abbreviated in the glossary. Use `cost_center_id` not `cc_id`; `is_finance_reviewer` not
`finance_reviewer_flag`; `ALL_WRITE` not `write_access`.

**RULE 2 — Architecture decisions are final.** The technology stack, hosting model,
authentication approach, data platform, and all decisions in Project Context Handoff v2.6
are final. Do not propose alternative frameworks, databases, or approaches. If you believe
a spec contains an error or contradiction, STOP and raise it as a question.

**RULE 3 — The frontend is a display layer only.** No authoritative business logic in the
frontend. No calculation result is trusted unless it came from the server-side calculation
service. Optimistic estimates are allowed while a server response is in flight, but always
reconcile to the server result. The frontend never constructs SQL, never reads the database
directly, never owns grant enforcement.

**RULE 4 — Business rules are stored as data, never hardcoded.** No compensation rate,
merit percentage, overhead allocation rate, or salary cap appears as a literal in code. All
values are read at runtime from `obs.merit_increase_rates`, `obs.compensation_burden_rates`,
`obs.compensation_component_mappings`, `obs.overhead_allocation_rates`.

**RULE 5 — Every API endpoint enforces the full 7-step authorization flow.** Step 1
validate token (401 on failure). Step 2 resolve user in `obs.users`, check `is_active`
(403). Step 3 if `is_administrator` grant full access, skip 4–6. Step 4 capability flags
(403). Step 5 cost center scope (403). Step 6 standard grant + confidential grant (strip
fields or 403). Step 7 execute and write audit log. Never return 404 to obscure a resource
from an unauthorized user — use 403.

**RULE 6 — Every mutation writes an audit event.** Every create, update, delete writes an
append-only record to `obs.audit_log`. Never UPDATE or DELETE `obs.audit_log`. Audit writes
are synchronous within the same API call; if the audit write fails, the mutation fails
(transaction rollback).

**RULE 7 — Notification failures are non-fatal.** `obs.notifications` writes are wrapped in
try/except. A notification write failure must not roll back the triggering event. Log
WARNING on failure. The triggering API call returns success regardless.

**RULE 8 — The LOCAL_AUTH_BYPASS safety constraint.** `LOCAL_AUTH_BYPASS=true` is permitted
only on localhost. A startup assertion raises if it is true and the host is not localhost.
It cannot be set via API call, request header, or any runtime mechanism — environment
variable only, read once at startup.

**RULE 9 — OBS never writes to source systems.** No code path writes to the accounting
system or HR admin system under any circumstance. `obs.expense_accounts` and
`obs.account_hierarchy_nodes` have no UPDATE or DELETE API endpoints — return HTTP 405 for
any write attempt.

**RULE 10 — All ingestion operations are idempotent.** Running the same file through the
pipeline twice produces the same result — no duplicate records. PostgreSQL
`INSERT … ON CONFLICT DO UPDATE` (atomic upsert) is the required promotion mechanism. Use
SHA-256 content hash + file name as the deduplication key in `obs.ingestion_control`.

---

## Phase 1 Active Conventions

Phase 1 — Authentication, User Registry & Security Core — is now **In Progress**.

- The 7-step authorization flow (Security Spec v1.4 §9.1) is now enforced on every
  endpoint. Confirm all 7 steps are present and ordered on every router added this phase.
- Error codes follow Security Spec v1.4 §9.2: 401 (invalid/missing token), 403
  (insufficient permission or inactive user), 404 (genuinely non-existent resource only),
  409 (conflict), 422 (validation error).
- Run the **auth-flow-reviewer** subagent after implementing each endpoint batch.
- Run the **frontend-conventions-reviewer** subagent after implementing each UI component.
- Run the **terminology-guard** subagent over any new schema or model file.

---

## Technology Stack (do not deviate)

All decisions final (Architecture Spec v3.6 §2.4).

| Layer | Technology | Key constraint |
|---|---|---|
| Frontend framework | React 18 + TypeScript | Strict TS — no `any` in production; all props typed. |
| Frontend build | Vite | `vite.config.ts` proxies `/api/*` to `localhost:8000` in dev. |
| UI components | MUI (Material UI) v5 | Use MUI as specified in UX spec; no third-party substitutes. |
| Backend framework | Python 3.11 + FastAPI | Stateless REST API; all business logic server-side. |
| ASGI server (local) | Uvicorn | `uvicorn backend.app.main:app --reload --port 8000` |
| Local database | DuckDB ≥ 0.10 | `DB_ENGINE=duckdb`. Schemas identical to PostgreSQL. |
| Production database | Azure PostgreSQL Flexible Server | `DB_ENGINE=postgresql`. Same query logic as DuckDB path. |
| Backend API host | Azure App Service (Linux, Py 3.11) | Single API hosting track; no fallback. |
| Frontend hosting | Azure Static Web Apps | SPA static assets + CDN. |
| Batch processing | Azure Functions | Hourly actuals; daily employees; ad hoc hierarchies (blob trigger). |
| File landing zone | Azure Blob Storage | Source-system file drop; monitored by Functions. |
| Authentication | Microsoft Entra ID (OIDC/OAuth 2.0) | Corporate SSO; managed identity to Azure resources. |
| Excel export | openpyxl (Python) | Server-side only, never client-side. |

---

## Document Hierarchy and Reading Order

Consult in this order when detail is needed on any topic.

| Document | Version | What it governs |
|---|---|---|
| Project Context Handoff | v2.6 | All decisions, open-item resolutions, authoritative reading order. Read first in any new session. |
| System Context and Domain Glossary | v1.11 | Authoritative terminology. Every term in code, APIs, schemas, UI must match exactly. |
| Application Architecture Specification | v3.6 | Stack, component architecture, API conventions, calculation service pattern, audit log, performance targets. |
| Security and Access Control Specification | v1.4 | Capability flags, cost center grants, 7-step authorization flow, audit events, error standards. |
| UX and UI Specification | v1.5 | All screens, navigation, MUI choices, interaction patterns, UX acceptance criteria. |
| Data Integration Specification | v1.8 | File format contracts, ingestion pipeline, dimension/actuals schemas, quarantine and re-promotion. |
| Notification Mechanism Specification | v1.1 | `obs.notifications` schema, notification service, 8 events, bell + inbox UX, deep-link routing. |
| FR: Budget Planning | v1.1 | Budget versions, lines, overhead allocation, vendor attribution, targets, locking, submission. |
| FR: Workforce Planning | v1.1 | Positions, compensation calculations, lifecycle events, transfer workflow, personnel expense push. |
| FR: Reporting | v1.1 | Report definitions, viewer, annotations, saved filters, Excel export, authoring UI. |
| FR: Finance Approval Workflow | v1.2 | Finance review records, approval queue, approve/reject, bulk approve, version status resolution. |
| Build Sequencing Plan | v1.2 | 10-phase build order with deliverables, tables, endpoints, acceptance criteria per phase. |
| Local Dev Environment Specification | v1.1 | macOS toolchain, DuckDB substitute, mock auth, repo structure, Makefile, VS Code configs, testing. |
| Azure Cloud Migration Specification | v2.0 | Canonical Azure provisioning, identity, schema bootstrap, deployment, Functions, validation stages. |

---

## Repository Structure

The `infra/` directory is governed by the Phase Cloud Validation Strategy.

```
obs-azure/
├─ backend/                 # FastAPI application
│  ├─ app/
│  │  ├─ main.py            # FastAPI entry point
│  │  ├─ auth/              # Entra ID + local bypass middleware
│  │  ├─ routers/           # API route modules (one per domain)
│  │  ├─ services/          # calculation_service.py, notification_service.py
│  │  ├─ db/                # engine.py (DuckDB / PostgreSQL switch), helpers.py
│  │  └─ models/            # Pydantic request/response models
│  ├─ pipeline/             # actuals_pipeline.py, employees_pipeline.py, hierarchy_pipeline.py
│  ├─ tests/                # unit/, integration/, fixtures/, ac_registry.yaml
│  ├─ requirements.txt
│  └─ requirements-dev.txt
├─ frontend/                # React TypeScript SPA (Vite, MUI)
├─ schema/                  # bootstrap.sql (DuckDB), bootstrap_pg.sql (PostgreSQL), seed/
├─ infra/                   # Azure CLI provisioning scripts (see Cloud Validation Strategy)
├─ local-data/             # GIT-IGNORED: landing-zone/, archive/, error/, obs.duckdb
├─ .claude/agents/          # Claude Code subagents
├─ .env.local.example       # Template; .env.local is git-ignored
├─ Makefile                 # Local dev convenience commands
└─ README.md
```

---

## Phase Gate (Definition of Done)

No phase is complete with any unchecked item. Full checklist in Implementation Guide v1.2
§14. The **phase-gate-checker** subagent enforces this.

- All deliverables, API endpoints, and `obs.*` tables for the phase are implemented and correct.
- `make test-unit` passes (0 failures); calculation service 100% line coverage from Phase 4 on.
- `make test-integration` passes (0 failures).
- `make ac-coverage` exits 0 (all AC-* identifiers for the phase registered and passing).
- `make lint` and `make typecheck` pass (0 errors).
- No hardcoded rate values; no `LOCAL_AUTH_BYPASS` in non-local code paths.
- Every mutation in the phase writes the correct audit event, verified by an integration
  test querying `obs.audit_log`.

---

## Common Pitfalls (do not do these)

- Do not use HTTP 404 to hide unauthorized resources. Return 403 when a resource exists but
  the user is not authorized; 404 is only for genuinely non-existent resources.
- Do not hardcode any rate, cap, or mapping value. Every numeric business rule comes from
  the database.
- Do not introduce terminology synonyms. Match the Glossary v1.11 exactly.
- Do not place authoritative business logic in the frontend.
- Do not write to source systems under any circumstances.
- Do not let a notification failure roll back its triggering event.
- Do not modify `obs.audit_log` after write.

---

## Subagents

Active subagents live in `.claude/agents/`. As of Phase 1, the active set is
**spec-librarian**, **phase-gate-checker**, **azure-lifecycle-operator**,
**terminology-guard**, **auth-flow-reviewer**, and **frontend-conventions-reviewer**.
Additional review agents (audit-and-idempotency-reviewer, calculation-verifier) are added
at the phases noted in the Subagents Specification v0.2.
