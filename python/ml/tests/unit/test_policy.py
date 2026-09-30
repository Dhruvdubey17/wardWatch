import math

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from wardwatch_ml.policy import AlertPolicy, AlertPolicyState, alerts_for_stay

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("scores", "expected"),
    [
        # Below threshold throughout.
        ([0.1, 0.2, 0.3], [False, False, False]),
        # Crossing fires once, then the refractory period holds.
        ([0.1, 0.6, 0.7, 0.8, 0.2], [False, True, False, False, False]),
        # Still high after the 6-hour refractory period: a reminder fires at hour 7.
        ([0.6] * 9, [True, False, False, False, False, False, True, False, False]),
        # A missing score never alerts.
        ([math.nan, 0.9], [False, True]),
    ],
)
def test_threshold_and_refractory(scores: list[float], expected: list[bool]) -> None:
    policy = AlertPolicy(threshold=0.5, refractory_hours=6)
    hours = list(range(1, len(scores) + 1))
    assert alerts_for_stay(policy, hours, scores).tolist() == expected


def test_rise_margin_breaks_through_refractory() -> None:
    policy = AlertPolicy(threshold=0.5, refractory_hours=6, rise_margin=0.2)
    # Hour 1 alerts at 0.55; 0.70 is not 0.2 higher, 0.76 is; 0.9 is not 0.2 above 0.76.
    fired = alerts_for_stay(policy, [1, 2, 3, 4], [0.55, 0.70, 0.76, 0.9])
    assert fired.tolist() == [True, False, True, False]


def test_state_steps_like_the_batch_function() -> None:
    policy = AlertPolicy(threshold=5, refractory_hours=6, rise_margin=2)
    state = AlertPolicyState(policy)
    scores = [3, 5, 6, 7, 8, 5, 5, 5]
    stepped = [state.step(hour, score) for hour, score in enumerate(scores, start=1)]
    assert stepped == alerts_for_stay(policy, range(1, 9), scores).tolist()
    assert stepped == [False, True, False, True, False, False, False, False]
    assert state.last_alert_hour == 4


@given(
    scores=st.lists(st.floats(min_value=0, max_value=1), min_size=1, max_size=60),
    threshold=st.floats(min_value=0.05, max_value=0.95),
    refractory=st.integers(min_value=1, max_value=12),
    margin=st.floats(min_value=0.01, max_value=0.5),
)
def test_never_fires_inside_refractory_without_the_required_rise(
    scores: list[float], threshold: float, refractory: int, margin: float
) -> None:
    policy = AlertPolicy(threshold=threshold, refractory_hours=refractory, rise_margin=margin)
    hours = np.arange(1, len(scores) + 1)
    fired = alerts_for_stay(policy, hours, scores)
    last_hour = last_score = None
    for hour, score, alert in zip(hours, scores, fired, strict=True):
        if alert:
            assert score >= threshold
            if last_hour is not None and last_score is not None and hour - last_hour < refractory:
                assert score >= last_score + margin
            last_hour, last_score = hour, score
