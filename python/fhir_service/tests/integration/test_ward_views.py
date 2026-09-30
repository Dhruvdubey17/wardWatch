import asyncio
import copy
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from aiokafka import AIOKafkaConsumer
from fhir_kafka import create_topics, produce, unique_topics
from sqlalchemy import func, select
from wardwatch_fhir.app import create_app
from wardwatch_fhir.converter import convert
from wardwatch_fhir.db import create_engine, session_factory
from wardwatch_fhir.events import EventBus
from wardwatch_fhir.migrate import upgrade
from wardwatch_fhir.models import Score
from wardwatch_fhir.repository import store_converted
from wardwatch_fhir.scores_consumer import ScoresConsumer, store_score
from wardwatch_fhir.settings import Settings
from wardwatch_ml.contracts import contracts_dir

pytestmark = pytest.mark.integration

EXAMPLES = contracts_dir() / "examples"


def load(path: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((EXAMPLES / path).read_text())
    return loaded


def later_oru(control_id: str, timestamp: str, heart_rate: str) -> dict[str, Any]:
    payload = copy.deepcopy(load("hl7.validated/oru_r01.json"))
    payload["control_id"] = control_id
    for segment in payload["segments"]:
        if segment["id"] == "OBX":
            segment["fields"][13] = [[[timestamp]]]
            if segment["fields"][2][0][0][0] == "8867-4":
                segment["fields"][4] = [[[heart_rate]]]
    return payload


def score(icu_hour: int, total: int, hour_ending: str) -> dict[str, Any]:
    payload = load("ward.scores/model.json")
    payload["icu_hour"] = icu_hour
    payload["news2"] = {**payload["news2"], "total": total}
    payload["hour_ending"] = hour_ending
    return payload


@pytest.fixture
async def client(database_url: str) -> AsyncIterator[httpx.AsyncClient]:
    await asyncio.to_thread(upgrade, database_url)
    engine = create_engine(database_url)
    sessions = session_factory(engine)
    payloads = [
        load("hl7.validated/adt_a01.json"),
        load("hl7.validated/oru_r01.json"),
        later_oru("SIM000008", "20240316020000+0000", "130"),
    ]
    for payload in payloads:
        async with sessions.begin() as session:
            await store_converted(session, convert(payload))
    for hour in range(1, 15):
        await store_score(
            sessions, score(hour, hour % 8, f"2024-03-15T{8 + hour % 16:02d}:30:00+00:00")
        )
    app = create_app(Settings(run_consumers=False), sessions=sessions)
    alert = load("ward.alerts/model.json")
    assert await app.state.alerts.store(alert)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as http:
        yield http
    await engine.dispose()


async def test_census_lists_in_progress_beds_with_latest_values(client: httpx.AsyncClient) -> None:
    beds = (await client.get("/api/ward/census")).json()
    assert len(beds) == 1
    bed = beds[0]
    assert bed["bed"] == "ICU 01 A"
    assert bed["mrn"] == "MRN0001234"
    assert bed["name"] == {"family": "Lindgren", "given": "Ada M"}
    # The later message's heart rate wins.
    assert bed["vitals"]["heart_rate"]["value"] == 130.0
    assert bed["vitals"]["heart_rate"]["at"] == "2024-03-16T02:00:00+00:00"
    assert set(bed["vitals"]) == {
        "heart_rate",
        "respiratory_rate",
        "spo2",
        "systolic_bp",
        "temperature",
    }
    assert [point["icu_hour"] for point in bed["trend"]] == list(range(3, 15))
    assert bed["latest_score"]["icu_hour"] == 14
    assert bed["unresolved_alerts"] == 1


async def test_discharged_encounters_leave_the_census(
    client: httpx.AsyncClient, database_url: str
) -> None:
    engine = create_engine(database_url)
    async with session_factory(engine).begin() as session:
        await store_converted(session, convert(load("hl7.validated/adt_a03_with_warning.json")))
    await engine.dispose()
    assert (await client.get("/api/ward/census")).json() == []


async def test_vitals_window_is_anchored_at_the_latest_observation(
    client: httpx.AsyncClient,
) -> None:
    wide = (await client.get("/api/patients/MRN0001234/vitals", params={"hours": 24})).json()
    assert wide["encounter_id"] == "ENC000001"
    assert wide["window"]["end"] == "2024-03-16T02:00:00+00:00"
    heart = wide["series"]["8867-4"]
    assert heart["display"] == "Heart rate"
    assert [point["value"] for point in heart["points"]] == [112.0, 130.0]
    narrow = (await client.get("/api/patients/MRN0001234/vitals", params={"hours": 6})).json()
    assert [point["value"] for point in narrow["series"]["8867-4"]["points"]] == [130.0]
    assert len(wide["alerts"]) == 1
    assert wide["scores"]


async def test_vitals_errors(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/patients/nobody/vitals")).status_code == 404
    assert (
        await client.get("/api/patients/MRN0001234/vitals", params={"hours": 0})
    ).status_code == 422


async def test_scores_consumer_upserts_rescored_hours(
    database_url: str, kafka_bootstrap: str
) -> None:
    await asyncio.to_thread(upgrade, database_url)
    topics = unique_topics("ward.scores")
    await create_topics(kafka_bootstrap, list(topics.values()))
    first = score(5, 3, "2024-03-15T13:30:00+00:00")
    rescored = score(5, 7, "2024-03-15T13:30:00+00:00")
    await produce(
        kafka_bootstrap,
        topics["ward.scores"],
        [("MRN0001234", json.dumps(first).encode()), ("MRN0001234", json.dumps(rescored).encode())],
    )
    engine = create_engine(database_url)
    sessions = session_factory(engine)
    consumer = AIOKafkaConsumer(
        topics["ward.scores"],
        bootstrap_servers=kafka_bootstrap,
        group_id=f"scores-{uuid.uuid4().hex[:8]}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    bus = EventBus()
    await consumer.start()
    try:
        async with bus.subscribe() as events:
            worker = ScoresConsumer(consumer, sessions, bus)
            handled = 0
            for _ in range(20):
                handled += await worker.run_once(timeout_ms=500)
                if handled >= 2:
                    break
            assert events.qsize() == 2
    finally:
        await consumer.stop()
    async with sessions() as session:
        rows = (await session.execute(select(Score))).scalars().all()
        count = (await session.execute(select(func.count()).select_from(Score))).scalar_one()
    assert count == 1
    assert rows[0].news2_total == 7
    await engine.dispose()
