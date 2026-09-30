import json
import uuid
from datetime import UTC, datetime

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from fhir_kafka import create_topics, produce, read_all, unique_topics
from jsonschema import Draft202012Validator
from scorer_support import observation_records, stay_frame
from wardwatch_ml.bundle import ServingBundle
from wardwatch_ml.contracts import contracts_dir
from wardwatch_scorer.engine import ScoringEngine
from wardwatch_scorer.online import OnlineScorer
from wardwatch_scorer.service import ScorerService
from wardwatch_scorer.settings import Settings

pytestmark = pytest.mark.integration


async def test_service_publishes_scores_and_alerts(
    bundle: ServingBundle, kafka_bootstrap: str
) -> None:
    topics = unique_topics("fhir.observations", "ward.alerts", "ward.scores")
    await create_topics(kafka_bootstrap, list(topics.values()))
    records = observation_records(stay_frame("p000001.psv"), "E1")
    await produce(
        kafka_bootstrap,
        topics["fhir.observations"],
        [(r["mrn"], json.dumps(r).encode()) for r in records],
    )
    settings = Settings(
        kafka_bootstrap=kafka_bootstrap,
        observations_topic=topics["fhir.observations"],
        alerts_topic=topics["ward.alerts"],
        scores_topic=topics["ward.scores"],
        settle_seconds=0.3,
    )
    consumer = AIOKafkaConsumer(
        settings.observations_topic,
        bootstrap_servers=kafka_bootstrap,
        group_id=f"scorer-{uuid.uuid4().hex[:8]}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    producer = AIOKafkaProducer(bootstrap_servers=kafka_bootstrap)
    await consumer.start()
    await producer.start()
    engine = ScoringEngine(OnlineScorer(bundle), settle_seconds=0.3, wall=lambda: datetime.now(UTC))
    service = ScorerService(settings, engine, consumer, producer)
    try:
        read = 0
        for _ in range(100):
            read += await service.step()
            if read >= len(records) and engine.windows["E1"].last_scored_hour == 40:
                break
    finally:
        await consumer.stop()
        await producer.stop()

    scores = await read_all(kafka_bootstrap, settings.scores_topic, 40)
    alerts = await read_all(kafka_bootstrap, settings.alerts_topic, 1, timeout_s=5)
    assert [payload["icu_hour"] for _, payload in scores] == list(range(1, 41))
    assert alerts
    for topic, items in (("ward.scores", scores), ("ward.alerts", alerts)):
        validator = Draft202012Validator(
            json.loads((contracts_dir() / "schemas" / f"{topic}.schema.json").read_text())
        )
        for key, payload in items:
            assert key == "MRN-E1"
            assert list(validator.iter_errors(payload)) == []
