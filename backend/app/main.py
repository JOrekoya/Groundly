"""FastAPI entrypoint.

Step 3 of the build: an HTTP surface over the deterministic finance engine,
with no LLM anywhere in it. Every response is a pure function of the request
body, so the whole API is testable in-process with no network and no mocking.

Run it with::

    python -m uvicorn app.main:app --reload --port 8000

Interactive docs are at /docs once it is up.
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.finance.engine import calculate_deal_metrics
from app.schemas import (
    AnalyzeResponse,
    DealMetricsModel,
    DealScopeModel,
    SubjectPropertyModel,
    ValuationResponse,
)
from app.tools.comps import value_property
from app.tools.contract import CompStore, EmptyCompStore, InMemoryCompStore

#: The Vite dev server. Production origins get added at deploy time rather
#: than being guessed at here.
DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")

#: Comparable sales available to the valuation endpoint.
#:
#: Empty until a county extract is loaded, because standing a database up is
#: not a precondition for the finance engine being useful. With no comps the
#: valuation endpoint says so plainly rather than guessing, which is the same
#: answer it gives when a real property has no neighbours that sold.
_comp_store: CompStore = EmptyCompStore()

#: Where to find a cached comp extract, if one exists. Written by
#: backend/load_comps.py.
COMPS_FILE_ENV = "GROUNDLY_COMPS_FILE"


def load_comps(comps: list) -> None:
    """Install a comp set. Called at startup, by a loader script, or by a test."""
    global _comp_store
    _comp_store = InMemoryCompStore(comps)


def comp_store() -> CompStore:
    return _comp_store


def load_cached_comps() -> None:
    """Load a comp extract if one is configured.

    Absence is not an error. The finance engine is the product's core and does
    not need comps, so the service starts and serves /api/analyze either way,
    and /api/value answers honestly that it has nothing to go on.
    """
    configured = os.environ.get(COMPS_FILE_ENV)
    path = Path(configured) if configured else Path("comps.json")
    if not path.exists():
        return

    # Imported lazily: the loader pulls in the ingestion layer, which the API
    # has no other reason to depend on.
    from load_comps import load_file

    try:
        load_comps(load_file(path))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"warning: could not load comps from {path}: {exc}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_cached_comps()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Groundly",
    version="0.1.0",
    summary="Deterministic real estate deal analysis.",
    description=(
        "Every number here is computed in Python by a pure function. "
        "No model is involved in any endpoint on this service."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(DEV_ORIGINS),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """Turn a rejected scope into a 422 rather than a 500.

    DealScope validates itself and raises ValueError. Pydantic catches most bad
    input first, but the domain object owns rules pydantic does not express —
    such as the operating-expense rates summing below 1.0 — and those should
    still read as client error.
    """
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Liveness check, and a statement of what this service does not do."""
    store = comp_store()
    loaded = getattr(store, "comps", [])
    return {
        "status": "ok",
        "llm_in_request_path": False,
        "comps_loaded": len(loaded),
    }


@app.post("/api/value", response_model=ValuationResponse)
def value(subject: SubjectPropertyModel) -> ValuationResponse:
    """Estimate a property's value from comparable sales.

    Answers with ``estimated: false`` and a reason when the comps are too
    sparse or too far to support a number. That is a successful response, not
    an error: a refusal is the correct output for a property with no
    neighbours that sold, and dressing it up as a 404 would push callers
    toward treating it as a bug to be worked around.
    """
    result = value_property(comp_store(), subject.to_domain())
    return ValuationResponse.from_domain(result)


@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(scope_model: DealScopeModel) -> AnalyzeResponse:
    """Derive every metric for a deal.

    This is the whole slider path. The client sends the full scope on each
    change and renders what comes back, so the browser never holds a second
    copy of the finance math. A round trip to localhost costs a millisecond or
    two, which is well under the threshold where a drag stops feeling live.
    """
    scope = scope_model.to_domain()
    metrics = calculate_deal_metrics(scope)
    return AnalyzeResponse(
        scope=DealScopeModel.from_domain(scope),
        metrics=DealMetricsModel.from_domain(metrics, scope),
    )


@app.middleware("http")
async def add_timing_header(request: Request, call_next):
    """Report server compute time so a slow slider can be blamed accurately.

    If the dashboard ever feels laggy, this says whether the cost is in the
    engine or in the browser.
    """
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Compute-Ms"] = f"{elapsed_ms:.2f}"
    return response
