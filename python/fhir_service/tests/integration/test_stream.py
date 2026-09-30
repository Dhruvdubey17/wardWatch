import asyncio
import json
import socket
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import uvicorn
from wardwatch_fhir.app import create_app
from wardwatch_fhir.converter import convert
from wardwatch_fhir.db import create_engine, session_factory
from wardwatch_fhir.events import vitals_event
from wardwatch_fhir.migrate import upgrade
from wardwatch_fhir.settings import Settings
from wardwatch_ml.contracts import contracts_dir

pytestmark = pytest.mark.integration

EXAMPLES = contracts_dir() / "examples"


def load(path: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((EXAMPLES / path).read_text())
    return loaded


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


class LiveServer:
    def __init__(self, app: Any, port: int) -> None:
        self.app = app
        self.url = f"http://127.0.0.1:{port}"


@pytest.fixture
async def live(database_url: str) -> AsyncIterator[LiveServer]:
    await asyncio.to_thread(upgrade, database_url)
    engine = create_engine(database_url)
    app = create_app(
        Settings(run_consumers=False, sse_heartbeat_seconds=0.2), sessions=session_factory(engine)
    )
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    yield LiveServer(app, port)
    server.should_exit = True
    await task
    await engine.dispose()


async def read_events(response: httpx.Response, wanted: int) -> tuple[list[dict[str, str]], int]:
    events: list[dict[str, str]] = []
    heartbeats = 0
    current: dict[str, str] = {}
    async for line in response.aiter_lines():
        if line.startswith(":"):
            heartbeats += 1
        elif line == "":
            if current:
                events.append(current)
                current = {}
            if len([e for e in events if "event" in e]) >= wanted:
                break
        else:
            field, _, value = line.partition(": ")
            current[field] = value
    return events, heartbeats


async def test_stream_delivers_vitals_and_alert_events(live: LiveServer) -> None:
    async with (
        httpx.AsyncClient(base_url=live.url, timeout=10) as http,
        http.stream("GET", "/api/stream") as response,
    ):
        assert response.headers["content-type"].startswith("text/event-stream")
        await asyncio.sleep(0.1)
        event = vitals_event(convert(load("hl7.validated/oru_r01.json")))
        assert event is not None
        live.app.state.bus.publish(event)
        alert = load("ward.alerts/model.json")
        assert await live.app.state.alerts.store(alert)
        acknowledged = await http.post(
            f"/api/alerts/{alert['alert_id']}/acknowledge",
            json={"actor": "Dr A"},
            headers={"If-Match": '"1"'},
        )
        assert acknowledged.status_code == 200
        events, _ = await asyncio.wait_for(read_events(response, 3), timeout=10)
    retry, *messages = events
    assert retry == {"retry": "3000"}
    kinds = [(message["event"], json.loads(message["data"])) for message in messages]
    assert kinds[0][0] == "vitals"
    assert kinds[0][1]["mrn"] == "MRN0001234"
    assert kinds[0][1]["at"] == "2024-03-15T13:00:00+00:00"
    assert [(kind, data["status"]) for kind, data in kinds[1:]] == [
        ("alert", "open"),
        ("alert", "acknowledged"),
    ]
    assert [message["id"] for message in messages] == ["1", "2", "3"]


async def test_idle_stream_sends_keep_alive_comments(live: LiveServer) -> None:
    async with (
        httpx.AsyncClient(base_url=live.url, timeout=10) as http,
        http.stream("GET", "/api/stream") as response,
    ):
        heartbeats = 0
        async for line in response.aiter_lines():
            if line.startswith(":"):
                heartbeats += 1
                if heartbeats == 2:
                    break
    assert heartbeats == 2


def test_result_without_observations_has_no_vitals_event() -> None:
    assert vitals_event(convert(load("hl7.validated/adt_a01.json"))) is None
