"""Consumes hl7.validated: convert, store, publish Observations, then commit.

Offsets are committed only after the database transaction commits and the
Observations are published, so a crash anywhere in between replays the
message. Replays are harmless because every write is an idempotent upsert
and Observation IDs are stable; the scorer ignores repeated Observation IDs.
"""

import base64
import binascii
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, ConsumerRecord
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from wardwatch_fhir.converter import ConversionError, Converted, convert
from wardwatch_fhir.metrics import CONVERSIONS, DEADLETTERED, OBSERVATIONS_STORED
from wardwatch_fhir.payloads import deadletter_payload, observation_payload
from wardwatch_fhir.repository import encounter_start, store_converted
from wardwatch_fhir.settings import Settings

log = logging.getLogger(__name__)

OnStored = Callable[[Converted], Awaitable[None]]


def _encode(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


class ValidatedConsumer:
    def __init__(
        self,
        consumer: AIOKafkaConsumer,
        producer: AIOKafkaProducer,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        on_stored: OnStored | None = None,
        before_commit: Callable[[ConsumerRecord[bytes, bytes]], Awaitable[None]] | None = None,
    ) -> None:
        self._consumer = consumer
        self._producer = producer
        self._sessions = sessions
        self._settings = settings
        self._on_stored = on_stored
        # Test hook: runs after the database commit and before the offset
        # commit, so a test can crash the consumer at the worst moment.
        self._before_commit = before_commit

    async def run_once(self, timeout_ms: int = 1000) -> int:
        """Process one batch of records; returns how many were handled."""
        batches = await self._consumer.getmany(timeout_ms=timeout_ms)
        handled = 0
        for partition, records in batches.items():
            for record in records:
                await self.handle(record)
                if self._before_commit is not None:
                    await self._before_commit(record)
                await self._consumer.commit({partition: record.offset + 1})
                handled += 1
        return handled

    async def handle(self, record: ConsumerRecord[bytes, bytes]) -> None:
        raw = record.value or b""
        try:
            payload = json.loads(raw)
            converted = convert(payload)
        except ConversionError as error:
            await self._deadletter(
                code=error.code,
                detail=error.detail,
                control_id=payload.get("control_id"),
                payload=payload,
                raw=raw,
            )
            return
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            await self._deadletter(
                code="PAYLOAD_INVALID",
                detail=str(error)[:500],
                control_id=None,
                payload=None,
                raw=raw,
            )
            return

        async with self._sessions.begin() as session:
            await store_converted(session, converted)
            started = await encounter_start(session, converted.encounter_id)
        CONVERSIONS.labels(message_type=converted.message_type).inc()
        OBSERVATIONS_STORED.inc(len(converted.observations))
        for observation in converted.observations:
            await self._producer.send_and_wait(
                self._settings.observations_topic,
                key=converted.mrn.encode(),
                value=_encode(
                    observation_payload(
                        mrn=converted.mrn,
                        encounter_id=converted.encounter_id,
                        encounter_start=started.isoformat() if started else None,
                        message_time=converted.message_time,
                        observation=observation,
                    )
                ),
            )
        if self._on_stored is not None:
            await self._on_stored(converted)
        log.info(
            "stored message",
            extra={
                "control_id": payload.get("control_id"),
                "mrn": converted.mrn,
                "observations": len(converted.observations),
                "skipped_observations": converted.skipped_observations,
            },
        )

    async def _deadletter(
        self,
        *,
        code: str,
        detail: str,
        control_id: str | None,
        payload: dict[str, Any] | None,
        raw: bytes,
    ) -> None:
        # The original HL7 bytes travel in raw_base64; fall back to the Kafka
        # record itself when the payload could not be read.
        original = raw
        if payload is not None and isinstance(payload.get("raw_base64"), str):
            try:
                original = base64.b64decode(payload["raw_base64"], validate=True)
            except (binascii.Error, ValueError):
                original = raw
        DEADLETTERED.labels(error_code=code).inc()
        key = str(payload.get("mrn", "")) if payload else ""
        await self._producer.send_and_wait(
            self._settings.deadletter_topic,
            key=key.encode(),
            value=_encode(deadletter_payload(code, detail, control_id, original)),
        )
        log.warning("dead-lettered message", extra={"error_code": code, "control_id": control_id})
