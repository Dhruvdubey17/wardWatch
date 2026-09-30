import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from wardwatch_fhir.alert_service import ConflictError, PreconditionFailedError
from wardwatch_fhir.escalation import escalate_overdue, run_escalation

pytestmark = pytest.mark.unit

START = datetime(2024, 3, 15, 12, 0, tzinfo=UTC)


@dataclass
class FakeAlerts:
    """Alerts with arrival times; transitions are recorded instead of stored."""

    arrived: dict[str, datetime]
    status: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[str, str, str | None, datetime | None]] = field(default_factory=list)
    changed_by_someone_else: set[str] = field(default_factory=set)

    async def overdue(self, older_than: datetime) -> list[tuple[str, int]]:
        return [
            (alert_id, 1)
            for alert_id, arrived in self.arrived.items()
            if arrived < older_than and self.status.get(alert_id, "open") == "open"
        ]

    async def transition(self, alert_id: str, transition: str, **kwargs: Any) -> dict[str, Any]:
        if alert_id in self.changed_by_someone_else:
            raise PreconditionFailedError(
                {"id": alert_id, "status": "acknowledged", "updated_by": "Dr A"}
            )
        if self.status.get(alert_id, "open") != "open":
            raise ConflictError({"id": alert_id}, "not open")
        self.status[alert_id] = "escalated"
        self.calls.append((alert_id, kwargs["actor"], kwargs["reason"], kwargs["now"]))
        return {"id": alert_id, "status": "escalated"}


async def test_escalates_only_alerts_older_than_the_limit() -> None:
    alerts = FakeAlerts({"old": START, "fresh": START + timedelta(minutes=10)})
    now = START + timedelta(minutes=16)
    assert await escalate_overdue(alerts, now, timedelta(minutes=15)) == ["old"]
    assert alerts.calls == [("old", "system", "unacknowledged_timeout", now)]


async def test_nothing_is_overdue_exactly_at_the_limit() -> None:
    alerts = FakeAlerts({"a": START})
    assert (
        await escalate_overdue(alerts, START + timedelta(minutes=15), timedelta(minutes=15)) == []
    )


async def test_alert_changed_meanwhile_is_skipped() -> None:
    alerts = FakeAlerts({"a": START, "b": START}, changed_by_someone_else={"a"})
    assert await escalate_overdue(alerts, START + timedelta(hours=1), timedelta(minutes=15)) == [
        "b"
    ]


async def test_loop_uses_the_clock_and_stops() -> None:
    alerts = FakeAlerts({"a": START, "b": START + timedelta(minutes=20)})
    times = iter([START + timedelta(minutes=16), START + timedelta(minutes=36)])
    stop = asyncio.Event()
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            stop.set()

    await run_escalation(
        alerts,
        after=timedelta(minutes=15),
        interval_seconds=30.0,
        clock=lambda: next(times),
        stop=stop,
        sleep=sleep,
    )
    assert [call[0] for call in alerts.calls] == ["a", "b"]
    assert sleeps == [30.0, 30.0]
