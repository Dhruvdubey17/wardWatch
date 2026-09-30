import math

import numpy as np
import pandas as pd
import pytest
from wardwatch_ml.metrics import (
    add_alerts,
    auprc,
    auroc,
    bootstrap_interval,
    event_metrics,
    normalized_utility,
    stay_utility,
)
from wardwatch_ml.policy import AlertPolicy

pytestmark = pytest.mark.unit

# A septic stay of 12 hours first labelled at row 6, so t_sepsis = 6 + 6 = 12.
# Utility slopes from the challenge parameters:
#   m_1 = 1 / (-6 + 12) = 1/6,  b_1 = 2      (early true positives)
#   m_2 = -1 / (3 + 6) = -1/9,  b_2 = 1/3    (late true positives)
#   m_3 = -2 / 9,              b_3 = -4/3    (false negatives after t_sepsis - 6)
SEPTIC_LABELS = [0] * 6 + [1] * 6


def test_auroc_and_auprc_hand_cases() -> None:
    labels = [0, 0, 1, 1]
    scores = [0.1, 0.4, 0.35, 0.8]
    # Pairs (pos, neg): (0.35,0.1) win, (0.35,0.4) loss, (0.8,0.1) win, (0.8,0.4) win: 3/4.
    assert auroc(labels, scores) == pytest.approx(0.75)
    # Ranked 0.8(+) 0.4(-) 0.35(+) 0.1(-): precision at the positives is 1 and 2/3,
    # recall steps of 1/2 each, so AP = (1 + 2/3) / 2 = 5/6.
    assert auprc(labels, scores) == pytest.approx(5 / 6)


def test_utility_of_false_alarms_on_a_non_septic_stay() -> None:
    # Three positive predictions at -0.05 each.
    assert stay_utility([0] * 5, [0, 1, 0, 1, 1]) == pytest.approx(-0.15)


def test_utility_when_every_hour_is_missed() -> None:
    # False negatives at t = 7..11: u = -2/9 (t - 12) - 4/3 gives
    # -2/9, -4/9, -6/9, -8/9, -10/9, a total of -30/9.
    assert stay_utility(SEPTIC_LABELS, [0] * 12) == pytest.approx(-30 / 9)


def test_utility_with_one_early_and_one_late_prediction() -> None:
    predictions = [0] * 12
    predictions[6] = 1  # t = 6 = t_sepsis - 6: u = 6/6 = 1
    predictions[9] = 1  # t = 9: u = -(9 - 12)/9 + 1/3 = 2/3
    # Misses at t = 7, 8, 10, 11: -2/9 - 4/9 - 8/9 - 10/9 = -24/9.
    assert stay_utility(SEPTIC_LABELS, predictions) == pytest.approx(1 + 2 / 3 - 24 / 9)


def test_early_prediction_is_floored_at_the_false_alarm_cost() -> None:
    predictions = [1] + [0] * 11
    # t = 0: max(0/6, -0.05) = 0, then misses total -30/9.
    assert stay_utility(SEPTIC_LABELS, predictions) == pytest.approx(-30 / 9)


def test_normalized_utility_hand_case() -> None:
    predictions = [0] * 12
    predictions[6] = predictions[9] = 1
    # Observed -1 (above). Inaction -30/9. Best predicts every row:
    # sum t/6 for t = 0..6 is 21/6, plus 8/9 + 7/9 + 6/9 + 5/9 + 4/9 = 30/9, so 41/6.
    # (-1 + 10/3) / (41/6 + 10/3) = (7/3) / (61/6) = 14/61.
    assert normalized_utility([(SEPTIC_LABELS, predictions)]) == pytest.approx(14 / 61)


def test_normalized_utility_is_one_for_best_and_zero_for_inaction() -> None:
    best = [(SEPTIC_LABELS, [1] * 12), ([0] * 8, [0] * 8)]
    assert normalized_utility(best) == pytest.approx(1.0)
    assert normalized_utility([(SEPTIC_LABELS, [0] * 12)]) == pytest.approx(0.0)


def scored_cohort() -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    for patient_id, hours in (("septic", 40), ("clean", 24), ("unknown", 10)):
        rows += [
            {"site": "A", "patient_id": patient_id, "hour": hour, "score": 0.0}
            for hour in range(1, hours + 1)
        ]
    scored = pd.DataFrame(rows)
    alert_at = {("septic", 10), ("septic", 35), ("clean", 5), ("unknown", 2), ("unknown", 9)}
    scored["score"] = [
        1.0 if (row.patient_id, row.hour) in alert_at else 0.0 for row in scored.itertuples()
    ]
    outcomes = pd.DataFrame(
        [
            {
                "site": "A",
                "patient_id": "septic",
                "septic": True,
                "onset_known": True,
                "onset_hour": 30,
            },
            {
                "site": "A",
                "patient_id": "clean",
                "septic": False,
                "onset_known": False,
                "onset_hour": None,
            },
            {
                "site": "A",
                "patient_id": "unknown",
                "septic": True,
                "onset_known": False,
                "onset_hour": None,
            },
        ]
    )
    return scored, outcomes


def test_event_metrics_hand_case() -> None:
    scored, outcomes = scored_cohort()
    alerts = add_alerts(scored, AlertPolicy(threshold=0.5, refractory_hours=1))
    metrics = event_metrics(scored, alerts, outcomes)
    # The unknown-onset stay and its two alerts are left out.
    assert metrics.unknown_onset_excluded == 1
    assert (metrics.septic_patients, metrics.non_septic_patients) == (1, 1)
    # Hour 10 is within [30 - 48, 30]; hour 35 is after onset.
    assert metrics.event_sensitivity == 1.0
    assert metrics.median_lead_time_hours == 20.0
    assert metrics.alerts == 3
    # 3 alerts over (40 + 24) / 24 patient-days.
    assert metrics.alerts_per_patient_day == pytest.approx(3 / (64 / 24))
    # 1 alert over 24 non-septic hours, one day.
    assert metrics.false_alerts_per_patient_day == pytest.approx(1.0)
    assert metrics.ppv_per_alert == pytest.approx(1 / 3)


def test_event_metrics_without_alerts() -> None:
    scored, outcomes = scored_cohort()
    alerts = pd.Series(False, index=scored.index)
    metrics = event_metrics(scored, alerts, outcomes)
    assert metrics.event_sensitivity == 0.0
    assert math.isnan(metrics.median_lead_time_hours)
    assert math.isnan(metrics.ppv_per_alert)
    assert metrics.alerts_per_patient_day == 0.0


def test_alert_early_than_48_hours_does_not_count() -> None:
    rows = [
        {"site": "A", "patient_id": "p", "hour": h, "score": float(h == 1)} for h in range(1, 61)
    ]
    scored = pd.DataFrame(rows)
    outcomes = pd.DataFrame(
        [{"site": "A", "patient_id": "p", "septic": True, "onset_known": True, "onset_hour": 55}]
    )
    alerts = add_alerts(scored, AlertPolicy(threshold=0.5))
    # Hour 1 is 54 hours before onset, outside the window.
    assert event_metrics(scored, alerts, outcomes).event_sensitivity == 0.0


def test_bootstrap_interval_is_reproducible_and_brackets_the_estimate() -> None:
    values = {("A", f"p{i}"): float(i % 2) for i in range(200)}
    statistic = lambda keys: float(np.mean([values[k] for k in keys]))  # noqa: E731
    first = bootstrap_interval(list(values), statistic, resamples=300, seed=7)
    again = bootstrap_interval(list(values), statistic, resamples=300, seed=7)
    assert first == again
    assert first[0] < 0.5 < first[1]
    # Standard error of a proportion near 0.5 over 200 stays is about 0.035.
    assert first[1] - first[0] == pytest.approx(4 * 0.035, abs=0.03)


def test_bootstrap_interval_all_nan_is_nan() -> None:
    low, high = bootstrap_interval([("A", "p")], lambda _: float("nan"), resamples=5)
    assert math.isnan(low)
    assert math.isnan(high)
