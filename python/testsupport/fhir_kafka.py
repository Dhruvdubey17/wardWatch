"""Kafka for FHIR service integration tests: a local broker or testcontainers."""

import asyncio
import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from testcontainers.community.kafka import KafkaContainer

KAFKA_ENV = "WARDWATCH_TEST_KAFKA_BOOTSTRAP"


@contextmanager
def kafka_server() -> Iterator[str]:
    configured = os.environ.get(KAFKA_ENV)
    if configured:
        yield configured
        return
    with KafkaContainer("confluentinc/cp-kafka:7.6.1").with_kraft() as container:
        yield container.get_bootstrap_server()


def unique_topics(*names: str) -> dict[str, str]:
    suffix = uuid.uuid4().hex[:10]
    return {name: f"{name}.test-{suffix}" for name in names}


async def create_topics(bootstrap: str, topics: list[str], partitions: int = 3) -> None:
    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap)
    await admin.start()
    try:
        await admin.create_topics([NewTopic(name, partitions, 1) for name in topics])
    finally:
        await admin.close()


async def produce(bootstrap: str, topic: str, records: list[tuple[str, bytes]]) -> None:
    producer = AIOKafkaProducer(bootstrap_servers=bootstrap)
    await producer.start()
    try:
        for key, value in records:
            await producer.send_and_wait(topic, key=key.encode(), value=value)
    finally:
        await producer.stop()


async def read_all(
    bootstrap: str, topic: str, expected: int, timeout_s: float = 20.0
) -> list[tuple[str, dict[str, Any]]]:
    """Read from the beginning until `expected` records arrive or the timeout passes."""
    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=bootstrap,
        group_id=f"reader-{uuid.uuid4().hex[:8]}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    await consumer.start()
    records: list[tuple[str, dict[str, Any]]] = []
    try:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while len(records) < expected and asyncio.get_running_loop().time() < deadline:
            batch = await consumer.getmany(timeout_ms=500)
            for partition_records in batch.values():
                records += [
                    ((r.key or b"").decode(), json.loads(r.value or b"{}"))
                    for r in partition_records
                ]
    finally:
        await consumer.stop()
    return records
