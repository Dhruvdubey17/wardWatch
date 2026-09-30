"""Liveness, readiness and Prometheus metrics."""

import asyncio
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

router = APIRouter()
READINESS_TIMEOUT_SECONDS = 2.0


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """The process is up and serving requests."""
    return {"status": "ok"}


async def _database_ready(request: Request) -> str:
    try:
        async with request.app.state.sessions() as session:
            await asyncio.wait_for(session.execute(text("SELECT 1")), READINESS_TIMEOUT_SECONDS)
    except Exception as error:  # noqa: BLE001  # any driver error means not ready, and is reported
        return f"unavailable: {type(error).__name__}"
    return "ok"


async def _kafka_ready(request: Request) -> str:
    producer = getattr(request.app.state, "producer", None)
    if producer is None:
        return "not configured"
    try:
        await asyncio.wait_for(producer.client.fetch_all_metadata(), READINESS_TIMEOUT_SECONDS)
    except Exception as error:  # noqa: BLE001  # any client error means not ready, and is reported
        return f"unavailable: {type(error).__name__}"
    return "ok"


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Ready once the database answers and the Kafka producer can fetch metadata."""
    checks: dict[str, Any] = {
        "database": await _database_ready(request),
        "kafka": await _kafka_ready(request),
    }
    ready = checks["database"] == "ok" and checks["kafka"] in ("ok", "not configured")
    if getattr(request.app.state.settings, "run_consumers", False) and checks["kafka"] != "ok":
        ready = False
    return JSONResponse({"ready": ready, "checks": checks}, status_code=200 if ready else 503)


@router.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
