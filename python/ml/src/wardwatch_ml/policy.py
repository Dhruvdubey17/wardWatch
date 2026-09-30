"""The alert policy shared by offline evaluation and the online scorer.

An alert fires when the hour's score is at or above the threshold. After an
alert the patient is suppressed for `refractory_hours`, unless the score rises
by at least `rise_margin` above the score that raised the last alert. Once the
refractory period has passed, a score still above the threshold alerts again,
which acts as a reminder that the patient remains high risk.
"""

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

DEFAULT_REFRACTORY_HOURS = 6.0


@dataclass(frozen=True)
class AlertPolicy:
    threshold: float
    refractory_hours: float = DEFAULT_REFRACTORY_HOURS
    rise_margin: float = float("inf")


@dataclass
class AlertPolicyState:
    """One patient's policy state, stepped hour by hour."""

    policy: AlertPolicy
    last_alert_hour: float | None = None
    last_alert_score: float | None = None

    def step(self, hour: float, score: float) -> bool:
        """Return True when an alert fires for this hour's score."""
        if np.isnan(score) or score < self.policy.threshold:
            return False
        if self.last_alert_hour is not None and self.last_alert_score is not None:
            suppressed = hour - self.last_alert_hour < self.policy.refractory_hours
            risen = score >= self.last_alert_score + self.policy.rise_margin
            if suppressed and not risen:
                return False
        self.last_alert_hour = hour
        self.last_alert_score = score
        return True


def alerts_for_stay(
    policy: AlertPolicy, hours: npt.ArrayLike, scores: npt.ArrayLike
) -> npt.NDArray[np.bool_]:
    """Apply the policy to one stay's hourly scores, in hour order."""
    state = AlertPolicyState(policy)
    hour_values = np.asarray(hours, dtype=np.float64)
    score_values = np.asarray(scores, dtype=np.float64)
    return np.array(
        [state.step(float(h), float(s)) for h, s in zip(hour_values, score_values, strict=True)],
        dtype=bool,
    )
