"""Chooses alert thresholds on the training site and freezes them.

Two operating points per model. Matched burden: the threshold whose alerts per
patient-day equal NEWS2 >= 5 under the same policy on the training site.
Fixed sensitivity: the highest threshold that still alerts 80% of septic stays
with a known onset in the 48 hours before onset. Both are searched over
quantiles of the training-site scores and never see the test site. The
searches bisect, relying on burden and sensitivity falling as the threshold
rises; the refractory rule can make that only nearly true, so the chosen
point's achieved value is recorded rather than assumed.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from wardwatch_ml.metrics import add_alerts, event_metrics
from wardwatch_ml.policy import DEFAULT_REFRACTORY_HOURS, AlertPolicy

NEWS2_URGENT = 5.0
NEWS2_RISE_MARGIN = 2.0
DEFAULT_TARGET_SENSITIVITY = 0.80
CANDIDATE_QUANTILES = 400


@dataclass(frozen=True)
class OperatingPoint:
    name: str
    policy: AlertPolicy
    # What the search aimed for and what the chosen threshold gave, on the training site.
    target: float
    achieved: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _candidates(scores: pd.Series) -> np.ndarray:
    values = scores.dropna().to_numpy(dtype=np.float64)
    return np.unique(np.quantile(values, np.linspace(0.0, 1.0, CANDIDATE_QUANTILES)))


def burden_at(
    scored: pd.DataFrame, outcomes: pd.DataFrame, policy: AlertPolicy, score_column: str
) -> float:
    alerts = add_alerts(scored, policy, score_column)
    return event_metrics(scored, alerts, outcomes).alerts_per_patient_day


def news2_policy() -> AlertPolicy:
    return AlertPolicy(
        threshold=NEWS2_URGENT,
        refractory_hours=DEFAULT_REFRACTORY_HOURS,
        rise_margin=NEWS2_RISE_MARGIN,
    )


def _bisect_descending(candidates: np.ndarray, good: Callable[[float], bool]) -> int:
    """Index of the highest candidate for which `good` holds, assuming it holds
    for every lower candidate once it holds for one. -1 when none is good."""
    low, high = 0, len(candidates) - 1
    found = -1
    while low <= high:
        middle = (low + high) // 2
        if good(float(candidates[middle])):
            found, low = middle, middle + 1
        else:
            high = middle - 1
    return found


def choose_matched_burden(
    scored: pd.DataFrame,
    outcomes: pd.DataFrame,
    target_alerts_per_day: float,
    rise_margin: float,
    score_column: str = "score",
) -> OperatingPoint:
    """The candidate threshold whose burden is closest to the target.

    Burden falls as the threshold rises, so a bisection finds the highest
    threshold at or above the target burden, and the next one up is the only
    other candidate that can be closer.
    """
    candidates = _candidates(scored[score_column])
    burdens: dict[float, float] = {}

    def burden(threshold: float) -> float:
        if threshold not in burdens:
            policy = AlertPolicy(threshold, DEFAULT_REFRACTORY_HOURS, rise_margin)
            burdens[threshold] = burden_at(scored, outcomes, policy, score_column)
        return burdens[threshold]

    index = _bisect_descending(candidates, lambda t: burden(t) >= target_alerts_per_day)
    options = [i for i in (index, index + 1) if 0 <= i < len(candidates)]
    best = min(
        options, key=lambda i: (abs(burden(float(candidates[i])) - target_alerts_per_day), -i)
    )
    threshold = float(candidates[best])
    return OperatingPoint(
        name="matched_burden",
        policy=AlertPolicy(threshold, DEFAULT_REFRACTORY_HOURS, rise_margin),
        target=target_alerts_per_day,
        achieved=burden(threshold),
    )


def choose_fixed_sensitivity(
    scored: pd.DataFrame,
    outcomes: pd.DataFrame,
    rise_margin: float,
    target_sensitivity: float = DEFAULT_TARGET_SENSITIVITY,
    score_column: str = "score",
) -> OperatingPoint:
    """The highest candidate threshold whose event sensitivity reaches the target.

    Falls back to the lowest candidate when none reaches it.
    """
    candidates = _candidates(scored[score_column])

    def sensitivity(threshold: float) -> float:
        policy = AlertPolicy(threshold, DEFAULT_REFRACTORY_HOURS, rise_margin)
        alerts = add_alerts(scored, policy, score_column)
        return event_metrics(scored, alerts, outcomes).event_sensitivity

    index = max(0, _bisect_descending(candidates, lambda t: sensitivity(t) >= target_sensitivity))
    threshold = float(candidates[index])
    return OperatingPoint(
        name="fixed_sensitivity",
        policy=AlertPolicy(threshold, DEFAULT_REFRACTORY_HOURS, rise_margin),
        target=target_sensitivity,
        achieved=sensitivity(threshold),
    )
