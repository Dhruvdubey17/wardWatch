from pathlib import Path

import pandas as pd
import pytest
from wardwatch_ml.data import load_physionet
from wardwatch_ml.onset import stay_outcome, stay_outcomes

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "physionet"


def stay(labels: list[int], first_hour: int = 1) -> pd.DataFrame:
    hours = list(range(first_hour, first_hour + len(labels)))
    return pd.DataFrame({"site": "A", "patient_id": "p1", "hour": hours, "SepsisLabel": labels})


@pytest.mark.parametrize(
    ("labels", "first_hour", "septic", "known", "onset"),
    [
        # Never labelled.
        ([0, 0, 0, 0], 1, False, False, None),
        # Labelled from the first row: onset may be before the record began.
        ([1, 1, 1], 1, True, False, None),
        # First label at hour 3, so onset is hour 3 + 6 = 9.
        ([0, 0, 1, 1, 1], 1, True, True, 9),
        # Late onset near the end: first label at hour 10, onset hour 16,
        # after the last recorded hour.
        ([0] * 9 + [1], 1, True, True, 16),
        # Records that start after ICU hour 1 use ICULOS, not the row index:
        # first label at hour 6 (row 2), onset hour 12.
        ([0, 0, 1], 4, True, True, 12),
        # Unknown onset even when ICULOS starts late.
        ([1, 1], 5, True, False, None),
    ],
)
def test_stay_outcome_table(
    labels: list[int], first_hour: int, septic: bool, known: bool, onset: int | None
) -> None:
    outcome = stay_outcome(stay(labels, first_hour))
    assert outcome.septic is septic
    assert outcome.onset_known is known
    assert outcome.onset_hour == onset
    assert outcome.first_hour == first_hour
    assert outcome.last_hour == first_hour + len(labels) - 1


def test_fixture_outcomes() -> None:
    frame = load_physionet(FIXTURES)
    outcomes = {
        outcome.patient_id: outcome
        for outcome in (stay_outcome(rows) for _, rows in frame.groupby(["site", "patient_id"]))
    }
    # p000001 is first labelled at ICU hour 21, so onset is hour 27.
    assert outcomes["p000001"].onset_hour == 27
    assert outcomes["p000002"].septic
    assert not outcomes["p000002"].onset_known
    assert outcomes["p000003"].onset_hour is None
    unknown = sorted(p for p, o in outcomes.items() if o.septic and not o.onset_known)
    assert unknown == ["p000002", "p100003"]


def test_outcome_table_has_one_row_per_stay() -> None:
    table = stay_outcomes(load_physionet(FIXTURES))
    assert len(table) == 17
    assert table["onset_hour"].dtype == "Int64"
    assert int(table["septic"].sum()) == 6
    assert int(table["onset_known"].sum()) == 4
