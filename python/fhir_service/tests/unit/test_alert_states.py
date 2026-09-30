import asyncio

import pytest
from wardwatch_fhir.alert_states import (
    STATUSES,
    InvalidTransitionError,
    Status,
    Transition,
    etag,
    next_status,
    parse_etag,
)
from wardwatch_fhir.events import SUBSCRIBER_QUEUE_SIZE, EventBus

pytestmark = pytest.mark.unit

ALLOWED: dict[tuple[str, str], str] = {
    ("open", "acknowledge"): "acknowledged",
    ("open", "escalate"): "escalated",
    ("acknowledged", "escalate"): "escalated",
    ("acknowledged", "resolve"): "resolved",
    ("escalated", "resolve"): "resolved",
}
TRANSITION_NAMES: tuple[Transition, ...] = ("acknowledge", "escalate", "resolve")
EVERY_PAIR = [(status, transition) for status in STATUSES for transition in TRANSITION_NAMES]


@pytest.mark.parametrize(("status", "transition"), EVERY_PAIR)
def test_every_status_and_transition(status: Status, transition: Transition) -> None:
    if (status, transition) in ALLOWED:
        assert next_status(status, transition) == ALLOWED[(status, transition)]
    else:
        with pytest.raises(InvalidTransitionError) as raised:
            next_status(status, transition)
        assert raised.value.current == status
        assert transition in str(raised.value)


def test_resolved_is_terminal() -> None:
    transitions: tuple[Transition, ...] = ("acknowledge", "escalate", "resolve")
    for transition in transitions:
        with pytest.raises(InvalidTransitionError):
            next_status("resolved", transition)


@pytest.mark.parametrize(
    ("header", "version"),
    [
        ('"3"', 3),
        ('W/"3"', 3),
        (' "12" ', 12),
        ("3", None),
        ('"x"', None),
        ('""', None),
        ("", None),
    ],
)
def test_parse_etag(header: str, version: int | None) -> None:
    assert parse_etag(header) == version


def test_etag_round_trip() -> None:
    assert parse_etag(etag(7)) == 7


async def test_event_bus_fans_out_and_forgets_on_exit() -> None:
    bus = EventBus()
    async with bus.subscribe() as first, bus.subscribe() as second:
        assert bus.subscribers == 2
        bus.publish({"type": "alert", "n": 1})
        assert (await first.get())["n"] == 1
        assert (await second.get())["n"] == 1
    assert bus.subscribers == 0
    bus.publish({"type": "alert"})


async def test_slow_subscriber_drops_its_oldest_events() -> None:
    bus = EventBus()
    async with bus.subscribe() as queue:
        for number in range(SUBSCRIBER_QUEUE_SIZE + 5):
            bus.publish({"n": number})
        assert queue.qsize() == SUBSCRIBER_QUEUE_SIZE
        assert (await asyncio.wait_for(queue.get(), 1))["n"] == 5
