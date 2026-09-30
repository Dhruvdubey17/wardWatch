from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from wardwatch_scorer.window import Added, EncounterWindow, icu_hour

pytestmark = pytest.mark.unit

ADMITTED = datetime(2024, 3, 15, 8, 0, tzinfo=UTC)


def observation(identifier: str, code: str, at: datetime, value: float) -> dict[str, Any]:
    return {
        "id": identifier,
        "code": {"coding": [{"code": code}]},
        "effectiveDateTime": at.isoformat(),
        "valueQuantity": {"value": value},
    }


@pytest.mark.parametrize(
    ("offset", "hour"),
    [
        (timedelta(hours=1), 1),
        (timedelta(hours=5), 5),
        (timedelta(minutes=30), 1),
        (timedelta(hours=4, minutes=1), 5),
        (timedelta(hours=4, seconds=59, minutes=59), 5),
        (timedelta(0), 0),
        (timedelta(minutes=-5), 0),
    ],
)
def test_icu_hour_boundaries(offset: timedelta, hour: int) -> None:
    assert icu_hour(ADMITTED + offset, ADMITTED) == hour


def test_add_places_values_by_hour_and_variable() -> None:
    window = EncounterWindow("M1", "E1", ADMITTED)
    assert window.add(
        observation("a", "8867-4", ADMITTED + timedelta(hours=2), 90.0), "t1", 1.0
    ) == (Added.ACCEPTED, 2)
    assert window.newest_hour == 2
    rows = window.rows(3, history_hours=31)
    assert rows["hour"].tolist() == [1, 2, 3]
    assert rows["HR"].isna().tolist() == [True, False, True]
    assert rows.loc[1, "HR"] == 90.0
    assert set(rows["patient_id"]) == {"E1"}


def test_duplicate_unknown_and_before_admission() -> None:
    window = EncounterWindow("M1", "E1", ADMITTED)
    first = observation("a", "8867-4", ADMITTED + timedelta(hours=1), 90.0)
    window.add(first, "t", 1.0)
    assert window.add(first, "t", 2.0) == (Added.DUPLICATE, 0)
    assert (
        window.add(observation("b", "9999-9", ADMITTED + timedelta(hours=1), 1.0), "t", 2.0)[0]
        is Added.UNKNOWN_CODE
    )
    assert (
        window.add(observation("c", "8867-4", ADMITTED, 80.0), "t", 2.0)[0]
        is Added.BEFORE_ADMISSION
    )


def test_later_result_in_the_same_hour_wins() -> None:
    window = EncounterWindow("M1", "E1", ADMITTED)
    window.add(observation("b", "8867-4", ADMITTED + timedelta(minutes=50), 95.0), "t", 1.0)
    window.add(observation("a", "8867-4", ADMITTED + timedelta(minutes=20), 80.0), "t", 2.0)
    assert window.rows(1, 31).loc[0, "HR"] == 95.0


def test_result_for_a_scored_hour_is_late() -> None:
    window = EncounterWindow("M1", "E1", ADMITTED)
    window.last_scored_hour = 3
    assert window.add(
        observation("a", "8867-4", ADMITTED + timedelta(hours=2), 90.0), "t", 1.0
    ) == (Added.LATE, 2)


def test_rows_are_limited_to_history() -> None:
    window = EncounterWindow("M1", "E1", ADMITTED)
    assert window.rows(40, history_hours=31)["hour"].tolist() == list(range(10, 41))


def test_message_time_keeps_the_newest_per_hour() -> None:
    window = EncounterWindow("M1", "E1", ADMITTED)
    at = ADMITTED + timedelta(hours=1)
    window.add(observation("a", "8867-4", at, 90.0), "2026-01-01T00:00:02+00:00", 1.0)
    window.add(observation("b", "9279-1", at, 18.0), "2026-01-01T00:00:01+00:00", 1.0)
    assert window.message_times[1] == "2026-01-01T00:00:02+00:00"
