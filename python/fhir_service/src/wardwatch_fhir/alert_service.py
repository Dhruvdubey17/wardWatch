"""Stores alerts and applies workflow transitions with optimistic locking.

Each alert row carries a version. A transition names the version it expects
(from If-Match); the UPDATE only succeeds while that version is current, so
two clinicians acting at once cannot both win. Every successful transition
also writes an alert_events row in the same transaction.
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from wardwatch_fhir.alert_states import (
    InvalidTransitionError,
    Status,
    Transition,
    etag,
    next_status,
)
from wardwatch_fhir.events import EventBus
from wardwatch_fhir.metrics import (
    ALERT_EVENT_PUBLISH_FAILURES,
    ALERT_TRANSITIONS,
    OPEN_ALERTS,
    TIME_TO_ACKNOWLEDGE,
)
from wardwatch_fhir.models import Alert, AlertEvent

log = logging.getLogger(__name__)


class AlertNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class PreconditionFailedError(Exception):
    """The If-Match version is no longer current."""

    current: dict[str, Any]


@dataclass(frozen=True)
class ConflictError(Exception):
    """The transition is not allowed from the alert's current status."""

    current: dict[str, Any]
    detail: str


def alert_view(alert: Alert) -> dict[str, Any]:
    """The JSON the ward API returns for one alert."""
    return {
        "id": alert.id,
        "mrn": alert.mrn,
        "encounter_id": alert.encounter_id,
        "source": alert.source,
        "status": alert.status,
        "raised_at": alert.raised_at.isoformat(),
        "icu_hour": alert.icu_hour,
        "raw_score": alert.raw_score,
        "calibrated_probability": alert.calibrated_probability,
        "news2": alert.news2,
        "top_factors": alert.top_factors,
        "model_version": alert.model_version,
        "updated_at": alert.updated_at.isoformat(),
        "updated_by": alert.updated_by,
        "etag": etag(alert.version),
    }


def _row_from_payload(payload: dict[str, Any], stored_at: datetime) -> dict[str, Any]:
    return {
        "id": payload["alert_id"],
        "mrn": payload["mrn"],
        "encounter_id": payload["encounter_id"],
        "source": payload["source"],
        "raised_at": datetime.fromisoformat(payload["raised_at"]),
        "icu_hour": payload["icu_hour"],
        "raw_score": payload["raw_score"],
        "calibrated_probability": payload["calibrated_probability"],
        "news2": payload["news2"],
        "top_factors": payload["top_factors"],
        "model_version": payload["model_version"],
        "message_time": payload["message_time"],
        "status": "open",
        "version": 1,
        "updated_at": stored_at,
        "updated_by": None,
    }


class AlertService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        bus: EventBus,
        producer: AIOKafkaProducer | None,
        events_topic: str,
    ) -> None:
        self._sessions = sessions
        self._bus = bus
        self._producer = producer
        self._events_topic = events_topic

    async def store(self, payload: dict[str, Any]) -> bool:
        """Store a ward.alerts record as open; False when the alert ID was already stored."""
        now = datetime.now(UTC)
        async with self._sessions.begin() as session:
            result = await session.execute(
                insert(Alert)
                .values(_row_from_payload(payload, now))
                .on_conflict_do_nothing(index_elements=[Alert.id])
                .returning(Alert.id)
            )
            created = result.scalar_one_or_none() is not None
        if created:
            await self.refresh_open_gauge()
            self._bus.publish(
                {
                    "type": "alert",
                    "alert_id": payload["alert_id"],
                    "status": "open",
                    "mrn": payload["mrn"],
                }
            )
        return created

    async def list_alerts(self, statuses: list[str] | None) -> list[dict[str, Any]]:
        query = select(Alert).order_by(Alert.raised_at.desc(), Alert.id)
        if statuses:
            query = query.where(Alert.status.in_(statuses))
        async with self._sessions() as session:
            return [alert_view(alert) for alert in (await session.execute(query)).scalars()]

    async def get(self, alert_id: str) -> dict[str, Any]:
        async with self._sessions() as session:
            alert = await session.get(Alert, alert_id)
        if alert is None:
            raise AlertNotFoundError(alert_id)
        return alert_view(alert)

    async def transition(
        self,
        alert_id: str,
        transition: Transition,
        *,
        expected_version: int,
        actor: str,
        reason: str | None = None,
        note: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        moment = now or datetime.now(UTC)
        async with self._sessions.begin() as session:
            alert = await session.get(Alert, alert_id)
            if alert is None:
                raise AlertNotFoundError(alert_id)
            if alert.version != expected_version:
                raise PreconditionFailedError(alert_view(alert))
            current = cast(Status, alert.status)
            try:
                target = next_status(current, transition)
            except InvalidTransitionError as error:
                raise ConflictError(alert_view(alert), str(error)) from None
            result = await session.execute(
                update(Alert)
                .where(Alert.id == alert_id, Alert.version == expected_version)
                .values(
                    status=target, version=Alert.version + 1, updated_at=moment, updated_by=actor
                )
                .returning(Alert.id)
                .execution_options(synchronize_session=False)
            )
            if result.scalar_one_or_none() is None:
                # Another transaction changed the row between our read and write.
                await session.rollback()
                raise PreconditionFailedError(await self.get(alert_id))
            event = AlertEvent(
                alert_id=alert_id,
                transition=transition,
                from_status=current,
                to_status=target,
                actor=actor,
                reason=reason,
                note=note,
                occurred_at=moment,
            )
            session.add(event)
            stored_at = alert.updated_at if alert.version == 1 else None
        updated = await self.get(alert_id)
        ALERT_TRANSITIONS.labels(
            transition=transition, actor_kind="system" if actor == "system" else "clinician"
        ).inc()
        if transition == "acknowledge" and stored_at is not None:
            TIME_TO_ACKNOWLEDGE.observe((moment - stored_at).total_seconds())
        await self.refresh_open_gauge()
        await self._publish_event(
            {
                "schema_version": 1,
                "alert_id": alert_id,
                "transition": transition,
                "from_status": current,
                "to_status": target,
                "actor": actor,
                "reason": reason,
                "note": note,
                "occurred_at": moment.isoformat(),
            }
        )
        return updated

    async def _publish_event(self, payload: dict[str, Any]) -> None:
        """Send one ward.alert-events record (contracts/schemas/ward.alert-events.schema.json)."""
        self._bus.publish(
            {
                "type": "alert",
                "alert_id": payload["alert_id"],
                "status": payload["to_status"],
                "actor": payload["actor"],
            }
        )
        if self._producer is None:
            return
        # The alert_events row is the record of truth, so a failed publish is
        # counted and logged but does not undo the committed transition.
        try:
            await self._producer.send_and_wait(
                self._events_topic,
                key=str(payload["alert_id"]).encode(),
                value=json.dumps(payload).encode(),
            )
        except KafkaError:
            ALERT_EVENT_PUBLISH_FAILURES.inc()
            log.exception(
                "could not publish ward.alert-events", extra={"alert_id": payload["alert_id"]}
            )

    async def refresh_open_gauge(self) -> None:
        async with self._sessions() as session:
            count = (
                await session.execute(select(func.count()).where(Alert.status == "open"))
            ).scalar_one()
        OPEN_ALERTS.set(count)

    async def overdue(self, older_than: datetime) -> list[tuple[str, int]]:
        """Open alerts stored before `older_than`, with their versions."""
        async with self._sessions() as session:
            rows = await session.execute(
                select(Alert.id, Alert.version).where(
                    Alert.status == "open", Alert.updated_at < older_than
                )
            )
            return [(str(alert_id), int(version)) for alert_id, version in rows]
