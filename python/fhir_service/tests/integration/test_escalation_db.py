import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from wardwatch_fhir.alert_service import AlertService
from wardwatch_fhir.db import create_engine, session_factory
from wardwatch_fhir.escalation import escalate_overdue
from wardwatch_fhir.events import EventBus
from wardwatch_fhir.migrate import upgrade
from wardwatch_fhir.models import Alert, AlertEvent
from wardwatch_ml.contracts import contracts_dir

pytestmark = pytest.mark.integration


def payload(alert_id: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(
        (contracts_dir() / "examples" / "ward.alerts" / "news2.json").read_text()
    )
    loaded["alert_id"] = alert_id
    return loaded


async def test_overdue_open_alerts_are_escalated_by_system(database_url: str) -> None:
    await asyncio.to_thread(upgrade, database_url)
    engine = create_engine(database_url)
    sessions = session_factory(engine)
    service = AlertService(sessions, EventBus(), None, "ward.alert-events")
    for alert_id in (
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
    ):
        await service.store(payload(alert_id))
    acknowledged = await service.transition(
        "22222222-2222-4222-8222-222222222222", "acknowledge", expected_version=1, actor="Dr A"
    )
    assert acknowledged["status"] == "acknowledged"

    later = datetime.now(UTC) + timedelta(minutes=30)
    escalated = await escalate_overdue(service, later, timedelta(minutes=15))
    assert escalated == ["11111111-1111-4111-8111-111111111111"]
    # Running again finds nothing: the alert is no longer open.
    assert await escalate_overdue(service, later, timedelta(minutes=15)) == []

    async with sessions() as session:
        alert = await session.get(Alert, "11111111-1111-4111-8111-111111111111")
        events = (
            (await session.execute(select(AlertEvent).where(AlertEvent.actor == "system")))
            .scalars()
            .all()
        )
    assert alert is not None
    assert (alert.status, alert.updated_by, alert.version) == ("escalated", "system", 2)
    assert len(events) == 1
    assert events[0].reason == "unacknowledged_timeout"
    await engine.dispose()
