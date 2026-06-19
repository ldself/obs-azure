# OBS — local developer convenience commands (Local Dev Environment Spec v1.1 §8.5).
# DuckDB is the local engine; the same code runs against Azure PostgreSQL in the cloud.

# All Python tooling runs through the repo virtualenv by default, so `make` targets
# work without activating it. Override if your venv lives elsewhere, e.g.
#   make test VENV=/path/to/venv   or   make test PYTHON=python
VENV ?= .venv
PYTHON ?= $(VENV)/bin/python
DUCKDB_PATH ?= ./local-data/obs.duckdb

.PHONY: help api frontend frontend-install frontend-lint frontend-typecheck \
        frontend-build db-bootstrap db-seed db-reset dirs \
        pipeline-actuals pipeline-employees pipeline-hierarchy \
        test test-unit test-integration lint typecheck ac-coverage

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

api: ## Run the FastAPI backend (http://localhost:8000, docs at /docs, health at /health)
	$(PYTHON) -m uvicorn backend.app.main:app --reload --port 8000

frontend: ## Run the Vite frontend dev server (http://localhost:5173)
	cd frontend && npm run dev

frontend-install: ## Install frontend npm dependencies
	cd frontend && npm install

frontend-lint: ## Lint the frontend with ESLint (strict; no warnings)
	cd frontend && npm run lint

frontend-typecheck: ## Type-check the frontend with tsc (strict, no emit)
	cd frontend && npm run typecheck

frontend-build: ## Production build of the frontend SPA
	cd frontend && npm run build

dirs: ## Create the local-data landing-zone / archive / error directories
	mkdir -p local-data/landing-zone/actuals \
	         local-data/landing-zone/employees \
	         local-data/landing-zone/hierarchies \
	         local-data/archive \
	         local-data/error

db-bootstrap: ## Create the local DuckDB database from schema/bootstrap.sql
	$(PYTHON) -m backend.app.db.bootstrap

db-seed: ## Apply seed scripts in schema/seed/ (in filename order)
	@for f in $$(ls schema/seed/*.sql 2>/dev/null | sort); do \
		echo "Applying $$f"; \
		$(PYTHON) -c "from backend.app.db.engine import connect; from backend.app.db.bootstrap import apply_bootstrap; c=connect(); apply_bootstrap(c, '$$f'); c.close()"; \
	done

db-reset: ## Delete and rebuild the local DuckDB database, then re-seed
	rm -f "$(DUCKDB_PATH)"
	$(MAKE) db-bootstrap
	$(MAKE) db-seed

pipeline-actuals: ## Run the actuals pipeline against FILE=<path>
	@mkdir -p local-data/landing-zone/actuals
	@cp "$(FILE)" local-data/landing-zone/actuals/
	$(PYTHON) -c "from backend.pipeline import actuals_pipeline; actuals_pipeline.run('$(FILE)')"

pipeline-employees: ## Run the employees pipeline against FILE=<path>
	@mkdir -p local-data/landing-zone/employees
	@cp "$(FILE)" local-data/landing-zone/employees/
	$(PYTHON) -c "from backend.pipeline import employees_pipeline; employees_pipeline.run('$(FILE)')"

pipeline-hierarchy: ## Run the hierarchy pipeline against FILE=<path>
	@mkdir -p local-data/landing-zone/hierarchies
	@cp "$(FILE)" local-data/landing-zone/hierarchies/
	$(PYTHON) -c "from backend.pipeline import hierarchy_pipeline; hierarchy_pipeline.run('$(FILE)')"

test: ## Run all backend tests
	$(PYTHON) -m pytest backend/tests

test-unit: ## Run unit tests
	$(PYTHON) -m pytest backend/tests/unit

test-integration: ## Run integration tests (requires `make api` running)
	$(PYTHON) -m pytest backend/tests/integration

lint: ## Lint the backend (ruff) and, if installed, the frontend (ESLint)
	$(PYTHON) -m ruff check backend
	@if [ -d frontend/node_modules ]; then $(MAKE) frontend-lint; \
	else echo "skipping frontend-lint (run 'make frontend-install' first)"; fi

typecheck: ## Type-check the backend (mypy) and, if installed, the frontend (tsc)
	$(PYTHON) -m mypy backend/app
	@if [ -d frontend/node_modules ]; then $(MAKE) frontend-typecheck; \
	else echo "skipping frontend-typecheck (run 'make frontend-install' first)"; fi

ac-coverage: ## Verify every registered acceptance criterion has a passing test
	$(PYTHON) -m backend.tests.check_ac_coverage
