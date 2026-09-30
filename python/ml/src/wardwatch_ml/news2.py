"""NEWS2 (Royal College of Physicians, 2017), SpO2 scale 1.

Inputs are rounded half up to the chart's precision before banding: whole
numbers for respiratory rate, SpO2, systolic pressure and pulse, and one
decimal for temperature. PhysioNet values such as a pulse of 81.66 would
otherwise fall between the chart's integer bands.

Two proxies stand in for fields PhysioNet does not record. Consciousness is
always taken as Alert (0 points), and supplemental oxygen is FiO2 above 0.21.
A parameter with no value scores 0. All three make NEWS2 lower than a bedside
score would be, which the evaluation document discusses.

The functions take numpy arrays so the offline pipeline and the online scorer
call the same code on one hour or on a million.
"""

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

COMPONENTS = (
    "respiratory_rate",
    "spo2",
    "supplemental_o2",
    "systolic_bp",
    "pulse",
    "consciousness",
    "temperature",
)

URGENT_THRESHOLD = 5
EMERGENCY_THRESHOLD = 7
ROOM_AIR_FIO2 = 0.21


def _round_half_up(values: FloatArray, decimals: int) -> FloatArray:
    scale = 10.0**decimals
    # A small epsilon keeps 38.05 * 10 = 380.49999... from rounding down.
    return np.floor(values * scale + 0.5 + 1e-9) / scale


def _band(values: FloatArray, upper_bounds: list[float], points: list[int]) -> IntArray:
    """Points for the first band whose inclusive upper bound holds the value.

    `points` has one more entry than `upper_bounds`, for values above the last
    bound. Missing values score 0.
    """
    result = np.full(values.shape, points[-1], dtype=np.int64)
    assigned = np.zeros(values.shape, dtype=bool)
    for bound, score in zip(upper_bounds, points, strict=False):
        hit = ~assigned & (values <= bound)
        result[hit] = score
        assigned |= hit
    result[np.isnan(values)] = 0
    return result


def respiratory_rate_points(rate: FloatArray) -> IntArray:
    # <=8: 3, 9-11: 1, 12-20: 0, 21-24: 2, >=25: 3
    return _band(_round_half_up(rate, 0), [8, 11, 20, 24], [3, 1, 0, 2, 3])


def spo2_points(spo2: FloatArray) -> IntArray:
    # Scale 1: <=91: 3, 92-93: 2, 94-95: 1, >=96: 0
    return _band(_round_half_up(spo2, 0), [91, 93, 95], [3, 2, 1, 0])


def supplemental_o2_points(fio2: FloatArray) -> IntArray:
    # Air: 0, any supplemental oxygen: 2. FiO2 above room air is the proxy.
    points = np.where(fio2 > ROOM_AIR_FIO2, 2, 0).astype(np.int64)
    points[np.isnan(fio2)] = 0
    return points


def systolic_bp_points(systolic: FloatArray) -> IntArray:
    # <=90: 3, 91-100: 2, 101-110: 1, 111-219: 0, >=220: 3
    return _band(_round_half_up(systolic, 0), [90, 100, 110, 219], [3, 2, 1, 0, 3])


def pulse_points(pulse: FloatArray) -> IntArray:
    # <=40: 3, 41-50: 1, 51-90: 0, 91-110: 1, 111-130: 2, >=131: 3
    return _band(_round_half_up(pulse, 0), [40, 50, 90, 110, 130], [3, 1, 0, 1, 2, 3])


def temperature_points(temperature: FloatArray) -> IntArray:
    # <=35.0: 3, 35.1-36.0: 1, 36.1-38.0: 0, 38.1-39.0: 1, >=39.1: 2
    return _band(_round_half_up(temperature, 1), [35.0, 36.0, 38.0, 39.0], [3, 1, 0, 1, 2])


@dataclass(frozen=True)
class News2:
    components: dict[str, IntArray]
    total: IntArray
    # Any single parameter scoring 3: the low-medium clinical response trigger.
    single_parameter_three: npt.NDArray[np.bool_]

    @property
    def urgent(self) -> npt.NDArray[np.bool_]:
        return self.total >= URGENT_THRESHOLD

    @property
    def emergency(self) -> npt.NDArray[np.bool_]:
        return self.total >= EMERGENCY_THRESHOLD


def score_news2(
    *,
    respiratory_rate: FloatArray,
    spo2: FloatArray,
    fio2: FloatArray,
    systolic_bp: FloatArray,
    pulse: FloatArray,
    temperature: FloatArray,
) -> News2:
    """NEWS2 for aligned arrays of vital signs; NaN means not measured."""
    arrays = [
        np.asarray(values, dtype=np.float64)
        for values in (respiratory_rate, spo2, fio2, systolic_bp, pulse, temperature)
    ]
    rr, oxygen_saturation, inspired, systolic, heart_rate, temp = arrays
    components = {
        "respiratory_rate": respiratory_rate_points(rr),
        "spo2": spo2_points(oxygen_saturation),
        "supplemental_o2": supplemental_o2_points(inspired),
        "systolic_bp": systolic_bp_points(systolic),
        "pulse": pulse_points(heart_rate),
        # PhysioNet has no level of consciousness, so everyone is Alert.
        "consciousness": np.zeros(rr.shape, dtype=np.int64),
        "temperature": temperature_points(temp),
    }
    stacked = np.stack([components[name] for name in COMPONENTS])
    return News2(
        components=components,
        total=stacked.sum(axis=0),
        single_parameter_three=(stacked == 3).any(axis=0),
    )
