"""Causal hourly features shared by training, evaluation and the online scorer.

A feature at ICU hour t uses only that stay's rows at or before t. Every
window is computed from explicit shifted copies of the stay's own values, so
scoring one stay online gives exactly the numbers the offline pipeline gives
for the same stay inside a table of thousands.

Features per variable:
  last            forward-filled value, blank once older than max staleness
  measured        1 when a value was recorded this hour, else 0
  hours_since     hours since the last recorded value, blank past the cap
  delta_{k}h      last value minus the last value k hours earlier
  {min,max,mean}_{w}h  over values recorded in the last w hours
plus NEWS2 components and total from the staleness-limited last values, and
the ICU hour itself. Demographics are left out on purpose: online, the person
comes from Synthea, so PhysioNet's age would not match the patient record.
"""

import json
from dataclasses import asdict, dataclass, field

import numpy as np
import numpy.typing as npt
import pandas as pd

from wardwatch_ml.news2 import COMPONENTS, score_news2

FloatArray = npt.NDArray[np.float64]

MAPPED_VARIABLES = (
    "HR",
    "O2Sat",
    "Temp",
    "SBP",
    "MAP",
    "DBP",
    "Resp",
    "FiO2",
    "Lactate",
    "WBC",
    "Creatinine",
    "Platelets",
    "Bilirubin_total",
)

DEFAULT_STALENESS_HOURS = {
    "HR": 6,
    "O2Sat": 6,
    "Temp": 8,
    "SBP": 6,
    "MAP": 6,
    "DBP": 6,
    "Resp": 6,
    "FiO2": 12,
    "Lactate": 24,
    "WBC": 24,
    "Creatinine": 24,
    "Platelets": 24,
    "Bilirubin_total": 24,
}

# NEWS2 needs these PhysioNet variables; consciousness has no source and is
# always 0, so it is not a feature.
NEWS2_INPUTS = {
    "respiratory_rate": "Resp",
    "spo2": "O2Sat",
    "fio2": "FiO2",
    "systolic_bp": "SBP",
    "pulse": "HR",
    "temperature": "Temp",
}
NEWS2_FEATURE_COMPONENTS = tuple(name for name in COMPONENTS if name != "consciousness")

STAY_KEYS = ["site", "patient_id"]


@dataclass(frozen=True)
class FeatureSpec:
    variables: tuple[str, ...] = MAPPED_VARIABLES
    max_staleness_hours: dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_STALENESS_HOURS)
    )
    delta_hours: tuple[int, ...] = (1, 3, 6)
    window_hours: tuple[int, ...] = (6, 12)
    hours_since_cap: int = 24
    version: int = 1

    def feature_names(self) -> list[str]:
        names: list[str] = []
        for variable in self.variables:
            names += [f"{variable}_last", f"{variable}_measured", f"{variable}_hours_since"]
            names += [f"{variable}_delta_{hours}h" for hours in self.delta_hours]
            names += [
                f"{variable}_{statistic}_{hours}h"
                for hours in self.window_hours
                for statistic in ("min", "max", "mean")
            ]
        names += [f"news2_{name}" for name in NEWS2_FEATURE_COMPONENTS]
        names += ["news2_total", "icu_hour"]
        return names

    @property
    def history_hours(self) -> int:
        """Hours of history a stay needs for its latest row to match the offline value."""
        longest_fill = max(self.max_staleness_hours[variable] for variable in self.variables)
        look_back = max(
            *self.window_hours, max(self.delta_hours) + longest_fill, self.hours_since_cap
        )
        return look_back + 1

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "FeatureSpec":
        raw = json.loads(text)
        return cls(
            variables=tuple(raw["variables"]),
            max_staleness_hours={
                key: int(value) for key, value in raw["max_staleness_hours"].items()
            },
            delta_hours=tuple(raw["delta_hours"]),
            window_hours=tuple(raw["window_hours"]),
            hours_since_cap=int(raw["hours_since_cap"]),
            version=int(raw["version"]),
        )


def _check_hourly(frame: pd.DataFrame) -> None:
    same_stay = (frame["site"].to_numpy()[1:] == frame["site"].to_numpy()[:-1]) & (
        frame["patient_id"].to_numpy()[1:] == frame["patient_id"].to_numpy()[:-1]
    )
    steps = np.diff(frame["hour"].to_numpy())
    if np.any(same_stay & (steps != 1)):
        raise ValueError("rows must be sorted by stay and hour with no missing hours")


class _Shifter:
    """Shifted copies of per-row values that never reach into another stay.

    Each stay is laid out after `padding` blank rows, so looking back up to
    `padding` rows from any real row lands on the same stay or on a blank.
    """

    def __init__(self, stay_codes: npt.NDArray[np.int64], padding: int) -> None:
        self.padding = padding
        self.rows = len(stay_codes)
        self.positions = np.arange(self.rows) + (stay_codes + 1) * padding
        self.length = (
            self.rows + (int(stay_codes.max()) + 1 if self.rows else 0) * padding + padding
        )

    def lag(self, values: FloatArray, hours: int) -> FloatArray:
        padded = np.full(self.length, np.nan)
        padded[self.positions] = values
        lagged: FloatArray = padded[self.positions - hours]
        return lagged

    def window(self, values: FloatArray, hours: int) -> FloatArray:
        """A (rows, hours) matrix of this hour and the hours - 1 before it."""
        padded = np.full(self.length, np.nan)
        padded[self.positions] = values
        offsets = np.arange(hours)
        matrix: FloatArray = padded[self.positions[:, None] - offsets[None, :]]
        return matrix


def _window_statistics(window: FloatArray) -> tuple[FloatArray, FloatArray, FloatArray]:
    present = ~np.isnan(window)
    count = present.sum(axis=1)
    # fmin and fmax skip NaN and return NaN only when every value is NaN,
    # without the warnings nanmin raises on empty windows.
    minimum = np.fmin.reduce(window, axis=1)
    maximum = np.fmax.reduce(window, axis=1)
    total = np.where(present, window, 0.0).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(count > 0, total / count, np.nan)
    return minimum, maximum, mean


def build_features(frame: pd.DataFrame, spec: FeatureSpec | None = None) -> pd.DataFrame:
    """Features for every row of a long table sorted by site, patient_id and hour."""
    spec = spec or FeatureSpec()
    missing_inputs = set(NEWS2_INPUTS.values()) - set(spec.variables)
    if missing_inputs:
        raise ValueError(f"the spec must include the NEWS2 inputs {sorted(missing_inputs)}")
    if frame.empty:
        return pd.DataFrame(columns=[*STAY_KEYS, "hour", *spec.feature_names()])
    _check_hourly(frame)
    stay_codes = frame.groupby(STAY_KEYS, sort=False).ngroup().to_numpy().astype(np.int64)
    hours = frame["hour"].to_numpy().astype(np.float64)
    padding = max(*spec.window_hours, *spec.delta_hours)
    shifter = _Shifter(stay_codes, padding)
    stays = pd.Series(stay_codes)

    columns: dict[str, FloatArray] = {}
    last_values: dict[str, FloatArray] = {}
    for variable in spec.variables:
        raw = frame[variable].to_numpy().astype(np.float64)
        observed = ~np.isnan(raw)
        observed_hour = (
            pd.Series(np.where(observed, hours, np.nan)).groupby(stays).ffill().to_numpy()
        )
        filled = pd.Series(raw).groupby(stays).ffill().to_numpy()
        since = hours - observed_hour
        last = np.where(since <= spec.max_staleness_hours[variable], filled, np.nan)
        last_values[variable] = last

        columns[f"{variable}_last"] = last
        columns[f"{variable}_measured"] = observed.astype(np.float64)
        columns[f"{variable}_hours_since"] = np.where(since <= spec.hours_since_cap, since, np.nan)
        for lag in spec.delta_hours:
            columns[f"{variable}_delta_{lag}h"] = last - shifter.lag(last, lag)
        for window_hours in spec.window_hours:
            minimum, maximum, mean = _window_statistics(shifter.window(raw, window_hours))
            columns[f"{variable}_min_{window_hours}h"] = minimum
            columns[f"{variable}_max_{window_hours}h"] = maximum
            columns[f"{variable}_mean_{window_hours}h"] = mean

    news2 = score_news2(**{name: last_values[variable] for name, variable in NEWS2_INPUTS.items()})
    for name in NEWS2_FEATURE_COMPONENTS:
        columns[f"news2_{name}"] = news2.components[name].astype(np.float64)
    columns["news2_total"] = news2.total.astype(np.float64)
    columns["icu_hour"] = hours

    features = pd.DataFrame(columns, index=frame.index)[spec.feature_names()]
    keys = frame[[*STAY_KEYS, "hour"]]
    return pd.concat([keys, features], axis=1)
