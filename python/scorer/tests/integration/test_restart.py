import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from scorer_support import ADMITTED, observation_records, stay_frame
from wardwatch_fhir.app import create_app
from wardwatch_fhir.converter import Converted
from wardwatch_fhir.db import create_engine, session_factory
from wardwatch_fhir.migrate import upgrade
from wardwatch_fhir.repository import store_converted
from wardwatch_fhir.scores_consumer import store_score
from wardwatch_fhir.settings import Settings
from wardwatch_ml.bundle import ServingBundle
from wardwatch_scorer.engine import Outgoing, ScoringEngine
from wardwatch_scorer.online import OnlineScorer
from wardwatch_scorer.rebuild import rebuild

pytestmark = pytest.mark.integration

ENCOUNTER = "E9"


def fresh_engine(bundle: ServingBundle, clock: list[float]) -> ScoringEngine:
    return ScoringEngine(
        OnlineScorer(bundle),
        settle_seconds=1.0,
        monotonic=lambda: clock[0],
        wall=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )


def feed(
    engine: ScoringEngine, clock: list[float], records: list[dict[str, Any]], settle: bool
) -> list[Outgoing]:
    out: list[Outgoing] = []
    for record in records:
        clock[0] += 0.01
        out += engine.ingest(record)
    if settle:
        clock[0] += 5
        out += engine.tick()
    return out


def alert_keys(outgoing: list[Outgoing]) -> list[tuple[int, str, str]]:
    return sorted(
        (o.payload["icu_hour"], o.payload["source"], o.payload["alert_id"])
        for o in outgoing
        if o.kind == "alert"
    )


def converted_for(records: list[dict[str, Any]]) -> Converted:
    first = records[0]
    return Converted(
        mrn=first["mrn"],
        encounter_id=ENCOUNTER,
        message_type="ORU",
        trigger_event="R01",
        message_time="t",
        patient={
            "resourceType": "Patient",
            "id": first["mrn"],
            "identifier": [{"value": first["mrn"]}],
            "name": [{"family": "Test", "given": ["Nine"]}],
            "gender": "male",
        },
        encounter={
            "resourceType": "Encounter",
            "id": ENCOUNTER,
            "status": "in-progress",
            "subject": {"reference": f"Patient/{first['mrn']}"},
            "period": {"start": ADMITTED.isoformat()},
            "location": [{"location": {"display": "ICU 09 A"}}],
        },
        observations=[record["observation"] for record in records],
    )


async def test_restart_mid_stay_gives_identical_later_alerts(
    bundle: ServingBundle, database_url: str
) -> None:
    records = observation_records(stay_frame("p000009.psv"), ENCOUNTER)
    clock = [0.0]
    uninterrupted = alert_keys(feed(fresh_engine(bundle, clock), clock, records, settle=True))
    assert uninterrupted, "the fixture stay should alert at least once"

    await asyncio.to_thread(upgrade, database_url)
    db = create_engine(database_url)
    sessions = session_factory(db)
    app = create_app(Settings(run_consumers=False), sessions=sessions)
    cut = len(records) // 2

    # First scorer: half the stay, with everything it publishes stored the way
    # the FHIR service stores it. Observations reach the FHIR service a little
    # ahead of the scorer, as they do in the running pipeline.
    before: list[Outgoing] = []
    first_clock = [0.0]
    first = fresh_engine(bundle, first_clock)
    async with sessions.begin() as session:
        await store_converted(session, converted_for(records[: cut + 5]))
    before += feed(first, first_clock, records[:cut], settle=False)
    for item in before:
        if item.kind == "score":
            await store_score(sessions, item.payload)
        else:
            assert await app.state.alerts.store(item.payload)

    # Restart: a new scorer rebuilds from the FHIR API, then Kafka redelivers
    # a few uncommitted records before the rest of the stay.
    second_clock = [100.0]
    second = fresh_engine(bundle, second_clock)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fhir"
    ) as client:
        assert await rebuild(second, client) == 1
    after = feed(second, second_clock, records[cut - 5 :], settle=True)
    await db.dispose()

    assert alert_keys(before + after) == uninterrupted
