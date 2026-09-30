"""Escalates alerts that stay open, unacknowledged, for too long."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Protocol

from wardwatch_fhir.alert_service import ConflictError, PreconditionFailedError
from wardwatch_fhir.alert_states import SYSTEM_ACTOR, SYSTEM_ESCALATION_REASON, Transition

log = logging.getLogger(__name__)


class Alerts(Protocol):
    async def overdue(self, older_than: datetime) -> list[tuple[str, int]]: ...

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
    ) -> dict[str, Any]: ...


async def escalate_overdue(alerts: Alerts, now: datetime, after: timedelta) -> list[str]:
    """Escalate every alert still open `after` its arrival; returns their IDs."""
    escalated = []
    for alert_id, version in await alerts.overdue(now - after):
        try:
            await alerts.transition(
                alert_id,
                "escalate",
                expected_version=version,
                actor=SYSTEM_ACTOR,
                reason=SYSTEM_ESCALATION_REASON,
                note=f"open for more than {after.total_seconds() / 60:.0f} minutes",
                now=now,
            )
        except (PreconditionFailedError, ConflictError):
            # A clinician acted between our query and our write; their action wins.
            log.info("skipped escalation, alert changed meanwhile", extra={"alert_id": alert_id})
            continue
        escalated.append(alert_id)
    if escalated:
        log.info("escalated unacknowledged alerts", extra={"count": len(escalated)})
    return escalated


async def run_escalation(
    alerts: Alerts,
    *,
    after: timedelta,
    interval_seconds: float,
    clock: Callable[[], datetime],
    stop: asyncio.Event,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Check every `interval_seconds` until `stop` is set."""
    while not stop.is_set():
        await escalate_overdue(alerts, clock(), after)
        await sleep(interval_seconds)
