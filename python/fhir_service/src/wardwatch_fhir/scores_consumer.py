"""Consumes ward.scores into the scores table; offsets commit after the write."""

import json
from datetime import datetime
from typing import Any

from aiokafka import AIOKafkaConsumer
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from wardwatch_fhir.events import EventBus
from wardwatch_fhir.models import Score


def score_row(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "encounter_id": payload["encounter_id"],
        "icu_hour": payload["icu_hour"],
        "mrn": payload["mrn"],
        "hour_ending": datetime.fromisoformat(payload["hour_ending"]),
        "scored_at": datetime.fromisoformat(payload["scored_at"]),
        "news2_total": payload["news2"]["total"],
        "news2": payload["news2"],
        "raw_score": payload["raw_score"],
        "calibrated_probability": payload["calibrated_probability"],
        "model_version": payload["model_version"],
        "alerted": payload["alerted"],
    }


async def store_score(sessions: async_sessionmaker[AsyncSession], payload: dict[str, Any]) -> None:
    """Upsert one hour's score; a rescored hour (after a scorer restart) replaces the old one."""
    row = score_row(payload)
    statement = insert(Score).values(row)
    statement = statement.on_conflict_do_update(
        index_elements=[Score.encounter_id, Score.icu_hour],
        set_={
            key: statement.excluded[key] for key in row if key not in ("encounter_id", "icu_hour")
        },
    )
    async with sessions.begin() as session:
        await session.execute(statement)


class ScoresConsumer:
    def __init__(
        self, consumer: AIOKafkaConsumer, sessions: async_sessionmaker[AsyncSession], bus: EventBus
    ) -> None:
        self._consumer = consumer
        self._sessions = sessions
        self._bus = bus

    async def run_once(self, timeout_ms: int = 1000) -> int:
        batches = await self._consumer.getmany(timeout_ms=timeout_ms)
        handled = 0
        for partition, records in batches.items():
            for record in records:
                payload = json.loads(record.value or b"{}")
                await store_score(self._sessions, payload)
                self._bus.publish(
                    {
                        "type": "score",
                        "mrn": payload["mrn"],
                        "encounter_id": payload["encounter_id"],
                        "icu_hour": payload["icu_hour"],
                    }
                )
                await self._consumer.commit({partition: record.offset + 1})
                handled += 1
        return handled
