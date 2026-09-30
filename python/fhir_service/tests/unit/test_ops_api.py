from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from wardwatch_fhir.app import create_app
from wardwatch_fhir.settings import Settings

pytestmark = pytest.mark.unit


class BrokenSession:
    async def execute(self, *_: Any) -> None:
        raise ConnectionRefusedError("database is down")


@asynccontextmanager
async def broken_session() -> AsyncIterator[BrokenSession]:
    yield BrokenSession()


def client() -> httpx.AsyncClient:
    app = create_app(Settings(run_consumers=False), sessions=broken_session)  # type: ignore[arg-type]  # a stand-in session factory
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def test_healthz_does_not_need_the_database() -> None:
    async with client() as http:
        response = await http.get("/healthz")
    assert response.json() == {"status": "ok"}


async def test_readyz_is_503_when_the_database_is_down() -> None:
    async with client() as http:
        response = await http.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["checks"]["database"].startswith("unavailable")
    assert body["checks"]["kafka"] == "not configured"


async def test_metrics_exposes_service_counters() -> None:
    async with client() as http:
        response = await http.get("/metrics")
    assert response.headers["content-type"].startswith("text/plain")
    for name in (
        "wardwatch_fhir_messages_converted_total",
        "wardwatch_fhir_open_alerts",
        "wardwatch_fhir_msh7_to_alert_stored_seconds_bucket",
        "wardwatch_fhir_time_to_acknowledge_seconds_bucket",
    ):
        assert name in response.text
