import copy
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from wardwatch_ml.data import load_physionet
from wardwatch_ml.fingerprint import data_fingerprint
from wardwatch_ml.onset import stay_outcomes
from wardwatch_ml.policy import AlertPolicy
from wardwatch_ml.thresholds import (
    burden_at,
    choose_fixed_sensitivity,
    choose_matched_burden,
    news2_policy,
)

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "physionet"


def cohort(stays: int = 40, hours: int = 48, seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Half the stays septic with onset at hour 40; their scores ramp up from hour 20."""
    random = np.random.default_rng(seed)
    rows, outcomes = [], []
    for index in range(stays):
        septic = index % 2 == 0
        patient = f"p{index}"
        for hour in range(1, hours + 1):
            signal = max(0.0, (hour - 20) / 20) if septic else 0.0
            rows.append(
                {
                    "site": "A",
                    "patient_id": patient,
                    "hour": hour,
                    "score": signal + random.normal(0, 0.15),
                }
            )
        outcomes.append(
            {
                "site": "A",
                "patient_id": patient,
                "septic": septic,
                "onset_known": septic,
                "onset_hour": 40 if septic else None,
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(outcomes)


def test_matched_burden_lands_near_the_target() -> None:
    scored, outcomes = cohort()
    point = choose_matched_burden(scored, outcomes, target_alerts_per_day=1.5, rise_margin=0.3)
    assert point.name == "matched_burden"
    assert point.achieved == pytest.approx(1.5, abs=0.25)
    assert burden_at(scored, outcomes, point.policy, "score") == point.achieved


def test_fixed_sensitivity_is_the_highest_threshold_meeting_the_target() -> None:
    scored, outcomes = cohort()
    point = choose_fixed_sensitivity(scored, outcomes, rise_margin=0.3)
    assert point.achieved >= 0.8
    # A slightly higher threshold must fall short, or it would have been chosen.
    higher = AlertPolicy(point.policy.threshold + 0.05, 6, 0.3)
    from wardwatch_ml.metrics import add_alerts, event_metrics

    sensitivity = event_metrics(scored, add_alerts(scored, higher), outcomes).event_sensitivity
    assert sensitivity < 0.8 or sensitivity == point.achieved


def test_operating_points_are_frozen() -> None:
    scored, outcomes = cohort()
    point = choose_matched_burden(scored, outcomes, 1.0, rise_margin=0.3)
    snapshot = copy.deepcopy(point)
    with pytest.raises(AttributeError):
        point.policy.threshold = 0.0  # type: ignore[misc]  # asserting the dataclass is frozen
    # Using the point on other data leaves it untouched.
    other_scored, other_outcomes = cohort(seed=9)
    burden_at(other_scored, other_outcomes, point.policy, "score")
    assert point == snapshot


def test_news2_policy_uses_the_urgent_threshold() -> None:
    policy = news2_policy()
    assert (policy.threshold, policy.refractory_hours, policy.rise_margin) == (5.0, 6.0, 2.0)


def test_fingerprint_changes_with_data_and_names_its_sites() -> None:
    frame = load_physionet(FIXTURES)
    site_a = frame[frame["site"] == "A"]
    fingerprint = data_fingerprint(site_a)
    assert fingerprint.sites == ("A",)
    assert fingerprint.stays == 9
    assert fingerprint.rows == 298
    assert data_fingerprint(site_a.sample(frac=1.0, random_state=1)) == fingerprint
    changed = site_a.copy()
    changed.loc[changed.index[0], "HR"] = 999.0
    assert data_fingerprint(changed).sha256 != fingerprint.sha256
    assert data_fingerprint(frame).sites == ("A", "B")


def test_outcomes_for_fixtures_feed_the_search() -> None:
    frame = load_physionet(FIXTURES)
    outcomes = stay_outcomes(frame)
    scored = frame[["site", "patient_id", "hour"]].assign(score=frame["HR"].fillna(80.0))
    point = choose_fixed_sensitivity(scored, outcomes, rise_margin=5.0)
    assert 0 < point.policy.threshold < 200
