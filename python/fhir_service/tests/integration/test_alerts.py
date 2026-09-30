import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from fhir_kafka import create_topics, produce, read_all, unique_topics
from jsonschema import Draft202012Validator
from sqlalchemy import select
from wardwatch_fhir.alerts_consumer import AlertsConsumer
from wardwatch_fhir.app import create_app
from wardwatch_fhir.db import create_engine, session_factory
from wardwatch_fhir.migrate import upgrade
from wardwatch_fhir.models import AlertEvent
from wardwatch_fhir.settings import Settings
from wardwatch_ml.contracts import contracts_dir

pytestmark = pytest.mark.integration

BASE = "http://testserver"


def alert_payload(alert_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(
        (contracts_dir() / "examples" / "ward.alerts" / "model.json").read_text()
    )
    if alert_id:
        payload["alert_id"] = alert_id
    return payload


class Ward:
    def __init__(
        self, http: httpx.AsyncClient, app: Any, settings: Settings, bootstrap: str
    ) -> None:
        self.http = http
        self.app = app
        self.settings = settings
        self.bootstrap = bootstrap

    async def raise_alert(self, alert_id: str | None = None) -> dict[str, Any]:
        alert_id = alert_id or str(uuid.uuid4())
        assert await self.app.state.alerts.store(alert_payload(alert_id))
        response = await self.http.get(f"/api/alerts/{alert_id}")
        assert response.status_code == 200
        loaded: dict[str, Any] = response.json()
        return loaded

    async def act(
        self, alert: dict[str, Any], action: str, etag: str | None, **body: Any
    ) -> httpx.Response:
        headers = {} if etag is None else {"If-Match": etag}
        return await self.http.post(
            f"/api/alerts/{alert['id']}/{action}",
            json={"actor": "Dr. Priya Raman", **body},
            headers=headers,
        )


@pytest.fixture
async def ward(database_url: str, kafka_bootstrap: str) -> AsyncIterator[Ward]:
    await asyncio.to_thread(upgrade, database_url)
    topics = unique_topics("ward.alert-events", "ward.alerts")
    await create_topics(kafka_bootstrap, list(topics.values()))
    settings = Settings(
        alert_events_topic=topics["ward.alert-events"],
        alerts_topic=topics["ward.alerts"],
        run_consumers=False,
    )
    engine = create_engine(database_url)
    producer = AIOKafkaProducer(bootstrap_servers=kafka_bootstrap)
    await producer.start()
    app = create_app(settings, sessions=session_factory(engine), producer=producer)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as http:
        yield Ward(http, app, settings, kafka_bootstrap)
    await producer.stop()
    await engine.dispose()


async def test_full_workflow_writes_events_and_publishes_them(ward: Ward) -> None:
    alert = await ward.raise_alert()
    assert alert["status"] == "open"
    assert alert["etag"] == '"1"'

    acknowledged = await ward.act(alert, "acknowledge", alert["etag"], note="Reviewing lactate")
    assert acknowledged.status_code == 200
    assert acknowledged.headers["ETag"] == '"2"'
    assert acknowledged.json()["status"] == "acknowledged"
    assert acknowledged.json()["updated_by"] == "Dr. Priya Raman"

    escalated = await ward.act(alert, "escalate", '"2"', reason="senior_review_needed")
    assert escalated.json()["status"] == "escalated"
    resolved = await ward.act(alert, "resolve", '"3"')
    assert resolved.json()["status"] == "resolved"

    async with ward.app.state.sessions() as session:
        events = (await session.execute(select(AlertEvent).order_by(AlertEvent.id))).scalars().all()
    assert [(e.from_status, e.to_status) for e in events] == [
        ("open", "acknowledged"),
        ("acknowledged", "escalated"),
        ("escalated", "resolved"),
    ]
    assert events[0].note == "Reviewing lactate"
    assert events[1].reason == "senior_review_needed"

    published = await read_all(ward.bootstrap, ward.settings.alert_events_topic, 3)
    schema = json.loads((contracts_dir() / "schemas" / "ward.alert-events.schema.json").read_text())
    validator = Draft202012Validator(schema)
    assert [payload["transition"] for _, payload in published] == [
        "acknowledge",
        "escalate",
        "resolve",
    ]
    for key, payload in published:
        assert key == alert["id"]
        assert list(validator.iter_errors(payload)) == []


async def test_missing_and_malformed_if_match(ward: Ward) -> None:
    alert = await ward.raise_alert()
    assert (await ward.act(alert, "acknowledge", None)).status_code == 428
    assert (await ward.act(alert, "acknowledge", "1")).status_code == 400


async def test_stale_etag_is_412_and_names_who_changed_it(ward: Ward) -> None:
    alert = await ward.raise_alert()
    assert (await ward.act(alert, "acknowledge", alert["etag"])).status_code == 200
    stale = await ward.http.post(
        f"/api/alerts/{alert['id']}/escalate",
        json={"actor": "Dr. Omar Haddad", "reason": "other"},
        headers={"If-Match": alert["etag"]},
    )
    assert stale.status_code == 412
    body = stale.json()
    assert body["alert"]["updated_by"] == "Dr. Priya Raman"
    assert "Dr. Priya Raman" in body["detail"]
    assert stale.headers["ETag"] == '"2"'


async def test_transition_not_in_the_workflow_is_409(ward: Ward) -> None:
    alert = await ward.raise_alert()
    response = await ward.act(alert, "resolve", alert["etag"])
    assert response.status_code == 409
    assert response.json()["alert"]["status"] == "open"


async def test_escalation_reason_must_come_from_the_list(ward: Ward) -> None:
    alert = await ward.raise_alert()
    response = await ward.act(alert, "escalate", alert["etag"], reason="because")
    assert response.status_code == 422
    missing = await ward.act(alert, "escalate", alert["etag"])
    assert missing.status_code == 422


async def test_unknown_alert_is_404(ward: Ward) -> None:
    response = await ward.http.post(
        "/api/alerts/nope/acknowledge", json={"actor": "A"}, headers={"If-Match": '"1"'}
    )
    assert response.status_code == 404
    assert (await ward.http.get("/api/alerts/nope")).status_code == 404


async def test_concurrent_conflicting_updates_have_one_winner(ward: Ward) -> None:
    alert = await ward.raise_alert()
    attempts = [ward.act(alert, "acknowledge", alert["etag"]) for _ in range(5)]
    attempts += [ward.act(alert, "escalate", alert["etag"], reason="other") for _ in range(5)]
    responses = await asyncio.gather(*attempts)
    codes = sorted(response.status_code for response in responses)
    assert codes.count(200) == 1
    assert codes.count(412) == 9
    async with ward.app.state.sessions() as session:
        events = (await session.execute(select(AlertEvent))).scalars().all()
    assert len(events) == 1


async def test_list_alerts_filters_by_status(ward: Ward) -> None:
    first = await ward.raise_alert()
    await ward.raise_alert()
    await ward.act(first, "acknowledge", first["etag"])
    open_alerts = (await ward.http.get("/api/alerts", params={"status": "open"})).json()
    both = (await ward.http.get("/api/alerts", params={"status": "open,acknowledged"})).json()
    assert len(open_alerts) == 1
    assert len(both) == 2
    assert (await ward.http.get("/api/alerts", params={"status": "closed"})).status_code == 400


async def test_alerts_consumer_stores_each_alert_once(ward: Ward) -> None:
    alert_id = str(uuid.uuid4())
    record = json.dumps(alert_payload(alert_id)).encode()
    await produce(
        ward.bootstrap, ward.settings.alerts_topic, [("MRN0001234", record), ("MRN0001234", record)]
    )
    consumer = AIOKafkaConsumer(
        ward.settings.alerts_topic,
        bootstrap_servers=ward.bootstrap,
        group_id=f"alerts-{uuid.uuid4().hex[:8]}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    await consumer.start()
    try:
        worker = AlertsConsumer(consumer, ward.app.state.alerts)
        handled = 0
        for _ in range(20):
            handled += await worker.run_once(timeout_ms=500)
            if handled >= 2:
                break
    finally:
        await consumer.stop()
    assert handled == 2
    alerts = (await ward.http.get("/api/alerts")).json()
    assert [alert["id"] for alert in alerts] == [alert_id]
