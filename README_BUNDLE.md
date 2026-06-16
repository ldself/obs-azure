# OBS Claude Code Bundle — Setup

Ready-to-commit working files for the OPEX Budgeting System (OBS) build with Claude Code.
These are the live counterparts to the v0.1 specification documents.

## Contents

```
CLAUDE.md                         # Repo-root orientation (read by Claude Code automatically)
.claude/agents/
  spec-librarian.md               # Active
  phase-gate-checker.md           # Active
  azure-lifecycle-operator.md     # Active
infra/
  tier.env.example                # Copy to tier.env; fill in real values
  provision-example.sh            # Copy to provision.sh
  deploy-example.sh               # Copy to deploy.sh
  teardown-example.sh             # Copy to teardown.sh
.gitignore                        # Merge into repo-root .gitignore
```

The four deferred review agents (terminology-guard, auth-flow-reviewer,
audit-and-idempotency-reviewer, calculation-verifier, frontend-conventions-reviewer)
are defined in the Subagents Specification and added at the phases noted there.

## Placement

Place `CLAUDE.md`, `.claude/`, and `infra/` at the repository root. Merge `.gitignore`
into the existing repo-root `.gitignore`.

## One-time infra setup

```bash
cp infra/provision-example.sh infra/provision.sh
cp infra/deploy-example.sh    infra/deploy.sh
cp infra/teardown-example.sh  infra/teardown.sh
cp infra/tier.env.example     infra/tier.env
chmod +x infra/*.sh
# then edit infra/tier.env (and any working script specifics) with real
# subscription / region / tenant / client values
```

The working copies (`infra/*.sh`, `infra/tier.env`) are git-ignored; the `-example`
templates remain tracked.

## Per-phase cloud validation loop

```bash
./infra/provision.sh --phase <n> --tier validation
make test-integration                       # local: DuckDB + mock auth
./infra/deploy.sh --phase <n> --tier validation
# run integration tests against the deployed App Service URL (DB_ENGINE=postgresql)
./infra/teardown.sh --phase <n> --tier validation
```

## Prerequisites before first use

- Local toolchain set up per Local Dev Environment Spec v1.1 (Python 3.11, Node, DuckDB,
  `.env.local`, `make api` and `npm run dev` running).
- `schema/bootstrap_pg.sql` authored before the deploy step (open item, Azure Cloud
  Migration Spec v2.0 §12). Until then, `deploy.sh` warns and skips schema bootstrap.
- Azure CLI logged in (`az login`) with access to the target subscription.
