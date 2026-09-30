import asyncio
import copy
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, ConsumerRecord
from fhir_kafka import create_topics, produce, read_all, unique_topics
from jsonschema import Draft202012Validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from wardwatch_fhir.db import create_engine, session_factory
from wardwatch_fhir.migrate import upgrade
from wardwatch_fhir.models import Encounter, Observation, Patient
from wardwatch_fhir.settings import Settings
from wardwatch_fhir.validated_consumer import ValidatedConsumer
from wardwatch_ml.contracts import contracts_dir

pytestmark = pytest.mark.integration

EXAMPLES = contracts_dir() / "examples" / "hl7.validated"


def example(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((EXAMPLES / f"{name}.json").read_text())
    return loaded


def schema(topic: str) -> Draft202012Validator:
    return Draft202012Validator(
        json.loads((contracts_dir() / "schemas" / f"{topic}.schema.json").read_text())
    )


class ServiceUnderTest:
    def __init__(
        self, bootstrap: str, sessions: async_sessionmaker[AsyncSession], settings: Settings
    ) -> None:
        self.bootstrap = bootstrap
        self.sessions = sessions
        self.settings = settings
        self.group = f"fhir-test-{uuid.uuid4().hex[:8]}"

    async def consumer(
        self, **hooks: Any
    ) -> tuple[ValidatedConsumer, AIOKafkaConsumer, AIOKafkaProducer]:
        consumer = AIOKafkaConsumer(
            self.settings.validated_topic,
            bootstrap_servers=self.bootstrap,
            group_id=self.group,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
        )
        producer = AIOKafkaProducer(bootstrap_servers=self.bootstrap, enable_idempotence=True)
        await consumer.start()
        await producer.start()
        return (
            ValidatedConsumer(consumer, producer, self.sessions, self.settings, **hooks),
            consumer,
            producer,
        )

    async def count(self, model: type[Any]) -> int:
        async with self.sessions() as session:
            return int(
                (await session.execute(select(func.count()).select_from(model))).scalar_one()
            )


@pytest.fixture
async def service(database_url: str, kafka_bootstrap: str) -> AsyncIterator[ServiceUnderTest]:
    await asyncio.to_thread(upgrade, database_url)
    topics = unique_topics("hl7.validated", "hl7.deadletter", "fhir.observations")
    await create_topics(kafka_bootstrap, list(topics.values()))
    settings = Settings(
        validated_topic=topics["hl7.validated"],
        deadletter_topic=topics["hl7.deadletter"],
        observations_topic=topics["fhir.observations"],
    )
    engine = create_engine(database_url)
    yield ServiceUnderTest(kafka_bootstrap, session_factory(engine), settings)
    await engine.dispose()


async def drain(worker: ValidatedConsumer, expected: int, attempts: int = 20) -> int:
    handled = 0
    for _ in range(attempts):
        handled += await worker.run_once(timeout_ms=500)
        if handled >= expected:
            break
    return handled


def encoded(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode()


async def test_messages_are_stored_and_observations_published(service: ServiceUnderTest) -> None:
    await produce(
        service.bootstrap,
        service.settings.validated_topic,
        [("MRN0001234", encoded(example("adt_a01"))), ("MRN0001234", encoded(example("oru_r01")))],
    )
    worker, consumer, producer = await service.consumer()
    try:
        assert await drain(worker, 2) == 2
    finally:
        await consumer.stop()
        await producer.stop()
    assert await service.count(Patient) == 1
    assert await service.count(Encounter) == 1
    assert await service.count(Observation) == 13
    published = await read_all(service.bootstrap, service.settings.observations_topic, 13)
    assert len(published) == 13
    validator = schema("fhir.observations")
    for key, payload in published:
        assert key == "MRN0001234"
        assert list(validator.iter_errors(payload)) == []


async def test_unconvertible_messages_are_dead_lettered(service: ServiceUnderTest) -> None:
    broken = copy.deepcopy(example("adt_a01"))
    pv1 = next(s["fields"] for s in broken["segments"] if s["id"] == "PV1")
    pv1[18] = []
    await produce(
        service.bootstrap,
        service.settings.validated_topic,
        [("MRN0001234", encoded(broken)), ("x", b"not json")],
    )
    worker, consumer, producer = await service.consumer()
    try:
        assert await drain(worker, 2) == 2
    finally:
        await consumer.stop()
        await producer.stop()
    dead = await read_all(service.bootstrap, service.settings.deadletter_topic, 2)
    codes = sorted(payload["error_code"] for _, payload in dead)
    assert codes == ["PAYLOAD_INVALID", "VISIT_NUMBER_MISSING"]
    validator = schema("hl7.deadletter")
    for _, payload in dead:
        assert payload["stage"] == "fhir"
        assert list(validator.iter_errors(payload)) == []
    assert await service.count(Patient) == 0


class SimulatedCrashError(Exception):
    pass


async def test_crash_between_write_and_commit_replays_without_duplicates(
    service: ServiceUnderTest,
) -> None:
    await produce(
        service.bootstrap,
        service.settings.validated_topic,
        [("MRN0001234", encoded(example("oru_r01")))],
    )

    async def crash(record: ConsumerRecord[bytes, bytes]) -> None:
        raise SimulatedCrashError

    worker, consumer, producer = await service.consumer(before_commit=crash)
    with pytest.raises(SimulatedCrashError):
        await drain(worker, 1)
    await consumer.stop()
    await producer.stop()
    assert await service.count(Observation) == 13

    # The restarted consumer sees the uncommitted message again.
    worker, consumer, producer = await service.consumer()
    try:
        assert await drain(worker, 1) == 1
    finally:
        await consumer.stop()
        await producer.stop()
    assert await service.count(Observation) == 13
    # Observations were published twice; the scorer drops repeated IDs.
    published = await read_all(service.bootstrap, service.settings.observations_topic, 26)
    assert len(published) == 26
    assert len({payload["observation"]["id"] for _, payload in published}) == 13

    # The offset is now committed, so a third consumer has nothing to do.
    worker, consumer, producer = await service.consumer()
    try:
        assert await worker.run_once(timeout_ms=1500) == 0
    finally:
        await consumer.stop()
        await producer.stop()
