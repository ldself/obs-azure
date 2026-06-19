"""OBS FastAPI application entry point.

Phase 0 scope: a hosting-agnostic stub API that exposes a health endpoint
returning HTTP 200 (Build Sequencing Plan v1.2 §4.0.4) and wires the
DuckDB/PostgreSQL engine switch. Authentication middleware, the 7-step
authorization flow, and all business routers are introduced in later phases.
"""

# `from __future__ import annotations` makes Python treat every type hint in this
# file as text instead of evaluating it at runtime. That lets us write modern hint
# syntax (like `dict[str, str]`) on Python 3.11 without errors. It must be the very
# first statement in the file.
from __future__ import annotations

# Standard-library imports (these ship with Python — nothing to install):
import logging  # writes diagnostic messages to the console/log instead of print()
from collections.abc import AsyncIterator  # the type returned by our lifespan function
from contextlib import asynccontextmanager  # turns a function into a startup/shutdown hook

# Third-party imports (installed via requirements.txt):
from fastapi import FastAPI  # the web framework that turns Python functions into HTTP endpoints
from fastapi.middleware.cors import CORSMiddleware  # lets the browser frontend call this API

# Our own code, imported from elsewhere in this project:
from backend.app.config import assert_local_auth_bypass_safe  # the RULE 8 safety check
from backend.app.config import settings  # all configuration values, read from env vars at startup
from backend.app.db import engine  # the DuckDB-vs-PostgreSQL database switch


# Configure logging ONCE for the whole app. `settings.log_level` is a string like
# "INFO"; `getattr(logging, "INFO")` turns that string into the matching logging
# constant. If the string is invalid we fall back to logging.INFO. After this line,
# any logger in the app will print messages at the chosen level or higher.
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
# Grab a named logger. Using a fixed name ("obs.api") means all our log lines are
# tagged consistently and can be filtered later.
logger = logging.getLogger("obs.api")

# RULE 8: enforce the LOCAL_AUTH_BYPASS safety constraint once, at import/startup.
# This runs immediately when the file is imported (before the server accepts any
# request). If LOCAL_AUTH_BYPASS is on while NOT running on localhost, this raises
# and the app refuses to start — preventing auth from being bypassed in the cloud.
assert_local_auth_bypass_safe(settings)


# The "lifespan" function is FastAPI's startup/shutdown hook. Everything BEFORE the
# `yield` runs once when the server boots; everything AFTER `yield` would run once at
# shutdown (we have none yet). The `@asynccontextmanager` decorator is what lets a
# single function express both halves. The `_` parameter is the FastAPI app instance,
# named `_` to signal "given to us by FastAPI, but we don't use it."
@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Log which database engine and auth mode we booted with. The `%s` placeholders
    # are filled in by the two arguments after the message — the standard, efficient
    # way to log values. This is purely informational so an operator can confirm the
    # app started in the configuration they expected.
    logger.info(
        "OBS API starting — DB_ENGINE=%s, LOCAL_AUTH_BYPASS=%s",
        engine.engine_name(),
        settings.local_auth_bypass,
    )
    # `yield` hands control back to FastAPI so it can start serving requests. The
    # function "pauses" here for the entire life of the app.
    yield


# Create the actual application object. `app` is THE thing the server (Uvicorn) runs,
# referenced as `backend.app.main:app` in the run command. The title/version/description
# show up in the auto-generated API docs at /docs. We pass our `lifespan` function so
# FastAPI knows to run our startup logic.
app = FastAPI(
    title="OPEX Budgeting System API",
    version="0.0.0",  # Phase 0 stub
    description="OBS stateless REST API. All business logic is server-side.",
    lifespan=lifespan,
)

# Middleware wraps every request/response. CORS ("Cross-Origin Resource Sharing") is a
# browser security rule: by default a page served from one address may NOT call an API
# at a different address. The React frontend runs on a different origin than this API,
# so we explicitly allow the frontend's origins (from settings) to make calls. Without
# this, the browser would block the frontend's requests.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,  # exact origins permitted to call us
    allow_credentials=True,  # allow cookies/auth headers to be sent
    allow_methods=["*"],  # permit all HTTP verbs (GET, POST, ...)
    allow_headers=["*"],  # permit all request headers
)


# Define an HTTP endpoint. The decorator `@app.get("/health")` registers this function
# to handle GET requests to the URL path "/health". `tags=["platform"]` just groups it
# under "platform" in the /docs page. Whatever the function returns becomes the JSON
# response body. This is the only endpoint in Phase 0.
@app.get("/health", tags=["platform"])
def health() -> dict[str, str]:
    """Liveness probe for load balancers and monitoring.

    Returns HTTP 200 with the active engine. Intentionally does not depend on a
    database connection so the probe stays stable before the schema is
    bootstrapped (Build Sequencing Plan v1.2 §4.0.4).
    """
    return {"status": "ok", "engine": engine.engine_name()}
