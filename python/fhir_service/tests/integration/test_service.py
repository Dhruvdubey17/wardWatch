import asyncio
import json
import uuid
from typing import Any

import httpx
import pytest
from aiokafka import AIOKafkaProducer
from fhir_kafka import create_topics, produce, read_all, unique_topics
from wardwatch_fhir.app import create_app
from wardwatch_fhir.db import create_engine, session_factory
from wardwatch_fhir.migrate import upgrade
from wardwatch_fhir.settings import Settings
from wardwatch_ml.contracts import contracts_dir

pytestmark = pytest.mark.integration

EXAMPLES = contracts_dir() / "examples"


def load(path: str) -> bytes:
    return (EXAMPLES / path).read_bytes()


async def wait_for(check: Any, timeout: float = 20.0) -> Any:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        result = await check()
        if result:
            return result
        await asyncio.sleep(0.2)
    raise AssertionError("condition not met in time")


async def test_running_service_consumes_stores_and_serves(
    database_url: str, kafka_bootstrap: str
) -> None:
    await asyncio.to_thread(upgrade, database_url)
    topics = unique_topics(
        "hl7.validated",
        "hl7.deadletter",
        "fhir.observations",
        "ward.alerts",
        "ward.alert-events",
        "ward.scores",
    )
    await create_topics(kafka_bootstrap, list(topics.values()))
    settings = Settings(
        database_url=database_url,
        kafka_bootstrap=kafka_bootstrap,
        consumer_group=f"fhir-{uuid.uuid4().hex[:8]}",
        validated_topic=topics["hl7.validated"],
        deadletter_topic=topics["hl7.deadletter"],
        observations_topic=topics["fhir.observations"],
        alerts_topic=topics["ward.alerts"],
        alert_events_topic=topics["ward.alert-events"],
        scores_topic=topics["ward.scores"],
        run_consumers=True,
    )
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as http,
    ):
        ready = await http.get("/readyz")
        assert ready.status_code == 200, ready.json()
        await produce(
            kafka_bootstrap,
            topics["hl7.validated"],
            [
                ("MRN0001234", load("hl7.validated/adt_a01.json")),
                ("MRN0001234", load("hl7.validated/oru_r01.json")),
            ],
        )
        await produce(
            kafka_bootstrap,
            topics["ward.alerts"],
            [("MRN0001234", load("ward.alerts/model.json"))],
        )
        await produce(
            kafka_bootstrap,
            topics["ward.scores"],
            [("MRN0001234", load("ward.scores/model.json"))],
        )

        async def observations() -> int:
            response = await http.get("/fhir/Observation", params={"subject": "MRN0001234"})
            total: int = response.json()["total"]
            return total if total == 13 else 0

        assert await wait_for(observations) == 13

        async def alerts() -> list[Any]:
            listed: list[Any] = (await http.get("/api/alerts")).json()
            return listed

        (alert,) = await wait_for(alerts)
        beds = await wait_for(lambda: _census(http))
        assert beds[0]["latest_score"]["icu_hour"] == 5
        acknowledged = await http.post(
            f"/api/alerts/{alert['id']}/acknowledge",
            json={"actor": "Dr A"},
            headers={"If-Match": alert["etag"]},
        )
        assert acknowledged.status_code == 200
    published = await read_all(kafka_bootstrap, topics["fhir.observations"], 13)
    assert len(published) == 13
    events = await read_all(kafka_bootstrap, topics["ward.alert-events"], 1)
    assert json.loads(json.dumps(events[0][1]))["transition"] == "acknowledge"


async def _census(http: httpx.AsyncClient) -> list[Any]:
    beds: list[Any] = (await http.get("/api/ward/census")).json()
    return beds if beds and beds[0]["latest_score"] else []


async def test_readyz_reports_kafka(database_url: str, kafka_bootstrap: str) -> None:
    await asyncio.to_thread(upgrade, database_url)
    engine = create_engine(database_url)
    producer = AIOKafkaProducer(bootstrap_servers=kafka_bootstrap)
    await producer.start()
    app = create_app(
        Settings(run_consumers=False), sessions=session_factory(engine), producer=producer
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as http:
        response = await http.get("/readyz")
    await producer.stop()
    await engine.dispose()
    assert response.status_code == 200
    assert response.json()["checks"] == {"database": "ok", "kafka": "ok"}
