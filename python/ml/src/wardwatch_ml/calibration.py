"""Maps model margins (log-odds) to calibrated probabilities, and measures calibration.

Both calibrators are stored as plain numbers so a serving bundle can be JSON
and the online scorer needs no scikit-learn objects at run time.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal

import numpy as np
import numpy.typing as npt
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

FloatArray = npt.NDArray[np.float64]
DEFAULT_BINS = 10


def sigmoid(margin: npt.ArrayLike) -> FloatArray:
    values = np.asarray(margin, dtype=np.float64)
    return 1.0 / (1.0 + np.exp(-values))


@dataclass(frozen=True)
class PlattCalibrator:
    """p = sigmoid(slope * margin + intercept) (Platt 1999)."""

    slope: float
    intercept: float
    kind: Literal["platt"] = "platt"

    @classmethod
    def fit(cls, margin: npt.ArrayLike, labels: npt.ArrayLike) -> "PlattCalibrator":
        # An effectively unpenalized fit: the regularization default would
        # pull the slope toward zero and flatten the probabilities.
        model = LogisticRegression(C=1e6, solver="lbfgs", max_iter=1000)
        model.fit(np.asarray(margin, dtype=np.float64).reshape(-1, 1), np.asarray(labels))
        return cls(slope=float(model.coef_[0][0]), intercept=float(model.intercept_[0]))

    def probability(self, margin: npt.ArrayLike) -> FloatArray:
        return sigmoid(self.slope * np.asarray(margin, dtype=np.float64) + self.intercept)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class IsotonicCalibrator:
    """A monotone step function through (margin, probability) knots."""

    knots_x: tuple[float, ...]
    knots_y: tuple[float, ...]
    kind: Literal["isotonic"] = "isotonic"

    @classmethod
    def fit(cls, margin: npt.ArrayLike, labels: npt.ArrayLike) -> "IsotonicCalibrator":
        model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        model.fit(np.asarray(margin, dtype=np.float64), np.asarray(labels, dtype=np.float64))
        return cls(
            knots_x=tuple(float(x) for x in model.X_thresholds_),
            knots_y=tuple(float(y) for y in model.y_thresholds_),
        )

    def probability(self, margin: npt.ArrayLike) -> FloatArray:
        # np.interp clamps to the end knots, matching out_of_bounds="clip".
        return np.interp(np.asarray(margin, dtype=np.float64), self.knots_x, self.knots_y)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


Calibrator = PlattCalibrator | IsotonicCalibrator


def calibrator_from_dict(raw: Mapping[str, Any]) -> Calibrator:
    """Rebuild a calibrator from the JSON a serving bundle stores."""
    if raw["kind"] == "platt":
        return PlattCalibrator(slope=float(raw["slope"]), intercept=float(raw["intercept"]))
    if raw["kind"] == "isotonic":
        return IsotonicCalibrator(
            knots_x=tuple(float(x) for x in raw["knots_x"]),
            knots_y=tuple(float(y) for y in raw["knots_y"]),
        )
    raise ValueError(f"unknown calibrator kind {raw['kind']!r}")


def brier_score(labels: npt.ArrayLike, probability: npt.ArrayLike) -> float:
    label_values = np.asarray(labels, dtype=np.float64)
    return float(np.mean((np.asarray(probability, dtype=np.float64) - label_values) ** 2))


@dataclass(frozen=True)
class ReliabilityBin:
    lower: float
    upper: float
    count: int
    mean_predicted: float
    observed_rate: float


def reliability(
    labels: npt.ArrayLike, probability: npt.ArrayLike, bins: int = DEFAULT_BINS
) -> list[ReliabilityBin]:
    """Equal-width probability bins; the last bin includes 1.0."""
    label_values = np.asarray(labels, dtype=np.float64)
    predicted = np.asarray(probability, dtype=np.float64)
    edges = np.linspace(0.0, 1.0, bins + 1)
    index = np.clip(np.digitize(predicted, edges[1:-1], right=False), 0, bins - 1)
    result = []
    for position in range(bins):
        members = index == position
        count = int(members.sum())
        result.append(
            ReliabilityBin(
                lower=float(edges[position]),
                upper=float(edges[position + 1]),
                count=count,
                mean_predicted=float(predicted[members].mean()) if count else float("nan"),
                observed_rate=float(label_values[members].mean()) if count else float("nan"),
            )
        )
    return result


def expected_calibration_error(
    labels: npt.ArrayLike, probability: npt.ArrayLike, bins: int = DEFAULT_BINS
) -> float:
    """Count-weighted mean gap between predicted and observed rates across bins."""
    table = reliability(labels, probability, bins)
    total = sum(entry.count for entry in table)
    return float(
        sum(
            entry.count * abs(entry.mean_predicted - entry.observed_rate)
            for entry in table
            if entry.count
        )
        / total
    )
