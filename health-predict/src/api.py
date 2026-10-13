"""HTTP API for glucose predictions (FastAPI).

    uvicorn src.api:app --host 127.0.0.1 --port 8000      (or: make api)

The production models are loaded when the server starts, so the first prediction isn't slowed by loading them. If
they can't be loaded (none in models/, or a broken file), the server still starts: /health and /predict answer 503
and loading is retried on each request, so copying models in is enough (or POST /admin/reload after a promotion).

This system predicts glucose values for research and analysis. It is not a
medical device and must not be used to determine insulin doses or make treatment
decisions. No dosing, correction, basal or carbohydrate recommendations are made.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from src import DISCLAIMER, config, registry, serving

log = logging.getLogger("uvicorn.error")  # uvicorn's own logger, so startup messages show in its output


def load_models() -> dict:
    """The production models (cached by serving.production_models); 503 if they can't be loaded."""
    try:
        return serving.production_models()
    except Exception as e:  # noqa: BLE001 — report any loading problem
        raise HTTPException(503, f"models not available: {e}") from None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    try:
        models = serving.production_models()
        versions = {f"{h}m": f"{prod['model']}/{prod['version']}" for h, (_, _, _, prod) in models.items()}
        log.info("Loaded production models: %s", versions)
    except Exception as e:  # noqa: BLE001 — keep serving; /health reports it
        log.error("Production models not available, answering 503 until they load: %s", e)
    yield


app = FastAPI(
    lifespan=lifespan,
    title="T1D glucose prediction (research)",
    version="1.0",
    description=f"**{DISCLAIMER}**\n\nPredicts glucose 15, 30, 45 and 60 minutes ahead from recent CGM and insulin "
                "data, with 80% and 95% prediction intervals. Research use only.",
)


class GlucosePoint(BaseModel):
    timestamp: datetime
    glucose: float = Field(description="CGM reading, in `glucose_unit`")
    trend: int | None = Field(None, ge=-3, le=3, description="optional trend arrow, +3 fastest rise … −3 fastest fall")
    rate: float | None = Field(None, description="optional device rate of change, in `glucose_unit` per minute")


class InsulinEvent(BaseModel):
    timestamp: datetime = Field(description="bolus: when delivery completed (extended: when it started); "
                                            "basal_rate: when the rate took effect")
    type: Literal["bolus", "automated_correction", "manual_correction", "basal_rate"]
    units: float | None = Field(None, ge=0, le=50, description="bolus units (bolus types)")
    rate_u_per_h: float | None = Field(None, ge=0, le=15, description="commanded basal rate from this time (basal_rate)")
    profile_rate_u_per_h: float | None = Field(None, ge=0, le=15, description="scheduled basal rate (optional)")
    duration_minutes: float | None = Field(None, ge=0, le=480, description="extended bolus: programmed duration")
    standard_percent: float | None = Field(None, ge=0, le=100, description="extended bolus: share delivered at once")
    carbs_g: float | None = Field(None, ge=0, le=500)


class PredictRequest(BaseModel):
    timestamp: datetime = Field(description="prediction time T (ISO 8601 with timezone)")
    glucose: float | None = Field(None, description="current reading at T (optional if it is in recent_glucose)")
    glucose_unit: Literal["mmol/L", "mg/dL"] = "mmol/L"
    recent_glucose: list[GlucosePoint] = Field(default_factory=list, description="readings up to T; ideally 3+ hours")
    recent_insulin: list[InsulinEvent] = Field(default_factory=list,
                                               description="basal rate changes covering the last 5+ hours, and "
                                                           "boluses from the last 24 hours (at least the most "
                                                           "recent bolus, however old)")

    model_config = {"json_schema_extra": {"examples": [{
        "timestamp": "2026-10-07T09:00:00Z", "glucose": 7.8,
        "recent_glucose": [{"timestamp": "2026-10-07T08:55:00Z", "glucose": 7.6},
                           {"timestamp": "2026-10-07T08:50:00Z", "glucose": 7.4}],
        "recent_insulin": [{"timestamp": "2026-10-07T04:00:00Z", "type": "basal_rate", "rate_u_per_h": 0.4},
                           {"timestamp": "2026-10-07T07:30:00Z", "type": "bolus", "units": 4.2, "carbs_g": 50}],
    }]}}


def to_history(req: PredictRequest) -> serving.History:
    s = config.settings()
    f = 1.0 if req.glucose_unit == "mmol/L" else 1 / s["units"]["mgdl_per_mmol"]
    at = serving._utc(req.timestamp)
    pts = {serving._utc(p.timestamp): p.glucose * f for p in req.recent_glucose}
    trends = {serving._utc(p.timestamp): float(p.trend) for p in req.recent_glucose if p.trend is not None}
    rates = {serving._utc(p.timestamp): p.rate * f for p in req.recent_glucose if p.rate is not None}
    if req.glucose is not None:
        pts[at] = req.glucose * f
    boluses, basal = [], []
    for e in req.recent_insulin:
        t = serving._utc(e.timestamp)
        if e.type == "basal_rate":
            if e.rate_u_per_h is None:
                raise serving.InputError("basal_rate events need rate_u_per_h")
            basal.append((t, e.rate_u_per_h, e.profile_rate_u_per_h))
        else:
            if e.units is None:
                raise serving.InputError(f"{e.type} events need units")
            boluses.append({"time": t, "units": e.units, "type": e.type, "duration_minutes": e.duration_minutes,
                            "standard_percent": e.standard_percent, "carbs_g": e.carbs_g})
    return serving.History(at=at, glucose=sorted(pts.items()), boluses=boluses, basal=basal, glucose_trend=trends,
                           glucose_rate=rates)


@app.get("/")
def root() -> dict:
    return {"service": app.title, "disclaimer": DISCLAIMER,
            "endpoints": ["/predict", "/model", "/health", "/dashboard", "/docs"]}


@app.get("/health")
def health() -> dict:
    load_models()
    return {"status": "ok"}


@app.get("/model")
def model_info() -> dict:
    reg = registry.load_registry()
    return {"production": reg["production"], "disclaimer": DISCLAIMER}


LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost", "testclient"}


@app.post("/admin/reload")
def reload_models(request: Request) -> dict:
    """Load the current production models from the registry (after a promotion). Local requests only."""
    if request.client is None or request.client.host not in LOCAL_CLIENTS:
        raise HTTPException(403, "reload is only allowed from this machine")
    serving.production_models.cache_clear()
    load_models()
    return {"reloaded": True, "production": registry.load_registry()["production"]}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    """The last generated dashboard (python -m src.dashboard). Contains personal health data."""
    path = config.path(config.settings()["report"]["output_dir"]) / "dashboard.html"
    if not path.exists():
        raise HTTPException(404, "no dashboard yet: run python -m src.dashboard")
    return HTMLResponse(path.read_text())


@app.post("/predict")
def predict(req: PredictRequest) -> dict:
    load_models()
    try:
        return serving.predict(to_history(req))
    except serving.InputError as e:
        raise HTTPException(422, str(e)) from None
