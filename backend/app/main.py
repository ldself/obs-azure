"""OBS FastAPI application entry point.

Phase 0 scope: a hosting-agnostic stub API that exposes a health endpoint
returning HTTP 200 (Build Sequencing Plan v1.2 §4.0.4) and wires the
DuckDB/PostgreSQL engine switch. Authentication middleware, the 7-step
authorization flow, and all business routers are introduced in later phases.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import assert_local_auth_bypass_safe
from backend.app.config import settings
from backend.app.db import engine


logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger("obs.api")

# RULE 8: enforce the LOCAL_AUTH_BYPASS safety constraint once, at import/startup.
assert_local_auth_bypass_safe(settings)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info(
        "OBS API starting — DB_ENGINE=%s, LOCAL_AUTH_BYPASS=%s",
        engine.engine_name(),
        settings.local_auth_bypass,
    )
    yield


app = FastAPI(
    title="OPEX Budgeting System API",
    version="0.0.0",  # Phase 0 stub
    description="OBS stateless REST API. All business logic is server-side.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["platform"])
def health() -> dict[str, str]:
    """Liveness probe for load balancers and monitoring.

    Returns HTTP 200 with the active engine. Intentionally does not depend on a
    database connection so the probe stays stable before the schema is
    bootstrapped (Build Sequencing Plan v1.2 §4.0.4).
    """
    return {"status": "ok", "engine": engine.engine_name()}
