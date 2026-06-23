# OPEX Budgeting System (OBS)

OBS is an operating-expense budgeting and workforce-planning application: a stateless
FastAPI backend, a React + TypeScript single-page frontend, and a batch ingestion pipeline
for actuals, employees, and account hierarchies. It runs locally on DuckDB with mock
authentication and deploys to Azure on PostgreSQL with Microsoft Entra ID SSO.

> **New to this repo?** Read [CLAUDE.md](CLAUDE.md) first — it is the authoritative
> orientation (the 10 Absolute Rules, technology stack, document hierarchy, and phase gates).
> The full specifications live in [docs/](docs/).

---

## Status

**Phase 2 (Dimension Data & Ingestion Pipeline) is complete.**

- **Phase 0 (Foundations) — complete.** Hosting-agnostic FastAPI stub, DuckDB/PostgreSQL
  engine switch, schema bootstrap, `LOCAL_AUTH_BYPASS` safety check (RULE 8), pipeline and
  Azure Functions scaffolding, Makefile, and per-phase local + cloud validation harness.
- **Phase 1 (Authentication, User Registry & Security Core) — complete.** Microsoft Entra
  ID OIDC authentication middleware, the full 7-step authorization flow on every endpoint,
  `obs.users` user registry with `is_active` / `is_administrator` / capability flags,
  `/api/auth/*` and `/api/users/*` routers, the React login flow with `AuthContext`, an
  admin user-management page, and cloud validation confirmed against Azure App Service +
  Static Web Apps.
- **Phase 2 (Dimension Data & Ingestion Pipeline) — complete.** Expense account and account
  hierarchy dimension tables (`obs.expense_accounts`, `obs.account_hierarchy_nodes`), cost
  center hierarchy ingestion, actuals and employee ingestion pipelines with idempotent
  SHA-256 + filename deduplication, quarantine and automatic re-promotion, non-USD
  rejection, Teams + email pipeline alerts, `/api/dimensions/*` and `/api/ingestion/*`
  routers, and cloud validation confirmed against Azure.

The business routers, calculation service, and remaining frontend screens are built out
phase by phase per the [Build Sequencing Plan](docs/OBS_Build_Sequencing_Plan_v1_2.docx)
(10 phases total).

---

## Architecture

| Layer | Technology | Notes |
|---|---|---|
| Frontend | React 18 + TypeScript, Vite, MUI v5 | Display layer only — no authoritative business logic. |
| Backend | Python 3.11 + FastAPI (Uvicorn) | Stateless REST; all business logic server-side. |
| Local DB | DuckDB ≥ 0.10 | `DB_ENGINE=duckdb`; schema identical to PostgreSQL. |
| Production DB | Azure PostgreSQL Flexible Server | `DB_ENGINE=postgresql`; same query logic. |
| Hosting | Azure App Service + Static Web Apps | API + SPA static assets/CDN. |
| Batch | Azure Functions + Blob Storage | Hourly actuals, daily employees, ad hoc hierarchies. |
| Auth | Microsoft Entra ID (OIDC/OAuth 2.0) | Local dev uses `LOCAL_AUTH_BYPASS` (localhost only). |

The same query logic runs against DuckDB locally and PostgreSQL in Azure, so the local
environment is a faithful substitute for the cloud.

---

## Repository layout

```
backend/           FastAPI app (auth, routers, services, db, models), pipeline/, tests/
frontend/          React + TypeScript SPA (Vite, MUI)
pipeline-functions/ Azure Functions for blob-triggered actuals/employees/hierarchy ingestion
schema/            bootstrap.sql (DuckDB), bootstrap_pg.sql (PostgreSQL), seed/
infra/             Azure CLI provision / deploy / teardown scripts (*-example templates tracked)
phase_validation/  Per-phase local + cloud validation scripts (e.g. 00_foundations/)
docs/              Authoritative specifications (versioned .docx)
local-data/        GIT-IGNORED: landing-zone/, archive/, error/, obs.duckdb
.claude/agents/    Claude Code subagents (spec-librarian, phase-gate-checker, azure-lifecycle-operator,
                   terminology-guard, auth-flow-reviewer, frontend-conventions-reviewer)
Makefile           Local dev convenience commands (make help)
CLAUDE.md          Repo-root orientation, read first
```

---

## Local development

### Prerequisites

- Python 3.11 and a virtualenv (`.venv/` exists; activate with `source .venv/bin/activate`)
- Node.js (for the Vite frontend)
- DuckDB ≥ 0.10

### Setup

```bash
# 1. Python dependencies
pip install -r requirements-dev.txt

# 2. Environment configuration
cp .env.local.example .env.local        # then review values; LOCAL_AUTH_BYPASS=true is localhost-only

# 3. Initialize the local DuckDB database
make dirs            # create local-data landing-zone / archive / error directories
make db-bootstrap    # create the database from schema/bootstrap.sql
make db-seed         # apply schema/seed/*.sql (or `make db-reset` to rebuild + reseed)

# 4. Run the backend (DuckDB + mock auth) at http://localhost:8000 (docs at /docs)
make api

# 5. Run the frontend (proxies /api/* to localhost:8000) at http://localhost:5173
cd frontend && npm install && make frontend
```

Run `make help` to list all convenience targets.

Key `.env.local` settings (see [.env.local.example](.env.local.example) for the full set):

- `DB_ENGINE=duckdb`, `DUCKDB_PATH=./local-data/obs.duckdb`
- `LOCAL_AUTH_BYPASS=true` — mock auth, permitted **only** on localhost
- `LANDING_ZONE_PATH` / `ARCHIVE_PATH` / `ERROR_PATH` — local filesystem stand-ins for Azure Blob Storage

---

## Quality checks

```bash
make lint               # ruff check backend
make typecheck          # mypy backend/app
make test               # all backend tests (or test-unit / test-integration)
make ac-coverage        # verify every registered acceptance criterion has a passing test
pre-commit run --all-files
```

`make test-integration` requires the backend running (`make api`). Conventions are
config-enforced: 120-char lines, 4-space indent, one import per line, NumPy-style docstrings.

---

## Azure cloud validation

Each build phase is validated against a throwaway Azure tier before moving on. Working
scripts are created from the tracked `-example` templates and are git-ignored:

```bash
cp infra/provision-example.sh infra/provision.sh
cp infra/deploy-example.sh    infra/deploy.sh
cp infra/teardown-example.sh  infra/teardown.sh
cp infra/tier.env.example     infra/tier.env
chmod +x infra/*.sh
# edit infra/tier.env with real subscription / region / tenant / client values
```

Per-phase loop (requires `az login`):

```bash
./infra/provision.sh --phase <n> --tier validation
./infra/deploy.sh     --phase <n> --tier validation   # DB_ENGINE=postgresql
./infra/teardown.sh   --phase <n> --tier validation
```

The **azure-lifecycle-operator** subagent runs one step of this loop at a time; it never
chains steps and never targets production resources.

Per-phase validation scripts that exercise both the local stack and the cloud loop live
under `phase_validation/<phase>/` (e.g. `phase_validation/00_foundations/local/` boots
`make api` and asserts `/health`; `phase_validation/00_foundations/cloud/` drives the
`infra/` provision → integration → teardown sequence).

---

## Core invariants

These hold for every line of code (full text in [CLAUDE.md](CLAUDE.md)):

1. Terminology matches the Domain Glossary v1.11 exactly — no synonyms.
2. The frontend is a display layer; calculations are trusted only from the server.
3. Business rules (rates, caps, mappings) are read from the database, never hardcoded.
4. Every endpoint enforces the full 7-step authorization flow; unauthorized → 403, never 404.
5. Every mutation writes an append-only `obs.audit_log` event; the log is never updated or deleted.
6. Notification failures are non-fatal and never roll back their triggering event.
7. OBS never writes to source systems; ingestion is idempotent (SHA-256 + filename dedup).

---

## Documentation

The authoritative specifications are versioned in [docs/](docs/). Start with the
**Project Context Handoff**, then the **System Context and Domain Glossary**, then the
specification relevant to your task. [CLAUDE.md](CLAUDE.md) lists the full reading order.
</content>
</invoke>
