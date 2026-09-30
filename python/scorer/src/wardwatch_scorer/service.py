"""Moves records between Kafka and the scoring engine."""

import asyncio
import json
import logging
from datetime import UTC, datetime

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from wardwatch_scorer.engine import Outgoing, ScoringEngine
from wardwatch_scorer.metrics import ACTIVE_ENCOUNTERS, END_TO_END_LATENCY, record_lag
from wardwatch_scorer.settings import Settings

log = logging.getLogger(__name__)
TICK_SECONDS = 0.2


class ScorerService:
    def __init__(
        self,
        settings: Settings,
        engine: ScoringEngine,
        consumer: AIOKafkaConsumer,
        producer: AIOKafkaProducer,
    ) -> None:
        self._settings = settings
        self._engine = engine
        self._consumer = consumer
        self._producer = producer

    async def publish(self, outgoing: list[Outgoing]) -> None:
        for item in outgoing:
            topic = (
                self._settings.alerts_topic if item.kind == "alert" else self._settings.scores_topic
            )
            await self._producer.send_and_wait(
                topic, key=item.key.encode(), value=json.dumps(item.payload).encode()
            )
            if item.kind == "alert":
                self._observe_latency(str(item.payload.get("message_time", "")))

    @staticmethod
    def _observe_latency(message_time: str) -> None:
        if not message_time:
            return
        sent = datetime.fromisoformat(message_time)
        if sent.tzinfo is None:
            sent = sent.replace(tzinfo=UTC)
        END_TO_END_LATENCY.observe(max(0.0, (datetime.now(UTC) - sent).total_seconds()))

    async def step(self) -> int:
        """Consume one batch, publish what it makes ready, then commit; returns records read."""
        batches = await self._consumer.getmany(timeout_ms=int(TICK_SECONDS * 1000))
        handled = 0
        for partition, records in batches.items():
            for record in records:
                await self.publish(self._engine.ingest(json.loads(record.value or b"{}")))
                handled += 1
            # Offsets commit after publishing. A crash before this line replays
            # the records, and the window drops Observation IDs it has seen.
            await self._consumer.commit({partition: records[-1].offset + 1})
        await self.publish(self._engine.tick())
        ACTIVE_ENCOUNTERS.set(len(self._engine.windows))
        await record_lag(self._consumer)
        return handled

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self.step()
        log.info("scorer stopped")
