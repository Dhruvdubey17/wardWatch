import math
from collections.abc import Callable

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from wardwatch_ml.news2 import (
    FloatArray,
    IntArray,
    pulse_points,
    respiratory_rate_points,
    score_news2,
    spo2_points,
    supplemental_o2_points,
    systolic_bp_points,
    temperature_points,
)

pytestmark = pytest.mark.unit


Band = Callable[[FloatArray], IntArray]


def one(function: Band, value: float) -> int:
    return int(function(np.array([value], dtype=np.float64))[0])


# Every cell edge in the NEWS2 chart (RCP 2017), tested on both sides.
@pytest.mark.parametrize(
    ("value", "points"),
    [(8, 3), (9, 1), (11, 1), (12, 0), (20, 0), (21, 2), (24, 2), (25, 3), (4, 3), (40, 3)],
)
def test_respiratory_rate_bands(value: float, points: int) -> None:
    assert one(respiratory_rate_points, value) == points


@pytest.mark.parametrize(
    ("value", "points"), [(91, 3), (92, 2), (93, 2), (94, 1), (95, 1), (96, 0), (100, 0), (70, 3)]
)
def test_spo2_scale_1_bands(value: float, points: int) -> None:
    assert one(spo2_points, value) == points


@pytest.mark.parametrize(
    ("value", "points"),
    [(90, 3), (91, 2), (100, 2), (101, 1), (110, 1), (111, 0), (219, 0), (220, 3), (60, 3)],
)
def test_systolic_bands(value: float, points: int) -> None:
    assert one(systolic_bp_points, value) == points


@pytest.mark.parametrize(
    ("value", "points"),
    [(40, 3), (41, 1), (50, 1), (51, 0), (90, 0), (91, 1), (110, 1), (111, 2), (130, 2), (131, 3)],
)
def test_pulse_bands(value: float, points: int) -> None:
    assert one(pulse_points, value) == points


@pytest.mark.parametrize(
    ("value", "points"),
    [
        (35.0, 3),
        (35.1, 1),
        (36.0, 1),
        (36.1, 0),
        (38.0, 0),
        (38.1, 1),
        (39.0, 1),
        (39.1, 2),
        (41.0, 2),
        (33.0, 3),
    ],
)
def test_temperature_bands(value: float, points: int) -> None:
    assert one(temperature_points, value) == points


@pytest.mark.parametrize(("fio2", "points"), [(0.21, 0), (0.22, 2), (0.5, 2), (0.2, 0)])
def test_supplemental_oxygen_proxy(fio2: float, points: int) -> None:
    assert one(supplemental_o2_points, fio2) == points


@pytest.mark.parametrize(
    ("function", "value", "points"),
    [
        # Values between chart integers are rounded half up first.
        (pulse_points, 90.4, 0),
        (pulse_points, 90.5, 1),
        (respiratory_rate_points, 8.5, 1),
        (systolic_bp_points, 110.49, 1),
        (temperature_points, 38.04, 0),
        (temperature_points, 38.05, 1),
        (temperature_points, 35.04, 3),
    ],
)
def test_rounding_before_banding(function: Band, value: float, points: int) -> None:
    assert one(function, value) == points


@pytest.mark.parametrize(
    "function",
    [
        respiratory_rate_points,
        spo2_points,
        supplemental_o2_points,
        systolic_bp_points,
        pulse_points,
        temperature_points,
    ],
)
def test_missing_values_score_zero(function: Band) -> None:
    assert one(function, math.nan) == 0


def test_aggregate_and_triggers() -> None:
    nan = math.nan
    # Rows: all normal; a 3 in one parameter; urgent (5); emergency (7+).
    result = score_news2(
        respiratory_rate=np.array([16, 16, 22, 26]),
        spo2=np.array([97, 97, 94, 91]),
        fio2=np.array([0.21, nan, 0.21, 0.4]),
        systolic_bp=np.array([120, 88, 105, 95]),
        pulse=np.array([70, 70, 95, 120]),
        temperature=np.array([37.0, 37.0, 38.5, 39.5]),
    )
    # Row 3: 2 (RR 22) + 1 (SpO2 94) + 1 (SBP 105) + 1 (pulse 95) + 1 (38.5) = 6.
    # Row 4: 3 + 3 + 2 + 2 + 2 + 2 = 14.
    assert result.total.tolist() == [0, 3, 6, 14]
    assert result.single_parameter_three.tolist() == [False, True, False, True]
    assert result.urgent.tolist() == [False, False, True, True]
    assert result.emergency.tolist() == [False, False, False, True]
    assert result.components["consciousness"].tolist() == [0, 0, 0, 0]
    assert result.components["supplemental_o2"].tolist() == [0, 0, 0, 2]


# (function, normal value, lowest and highest value to explore)
MONOTONE_CASES = [
    (respiratory_rate_points, 16.0, 0.0, 60.0),
    (spo2_points, 100.0, 50.0, 100.0),
    (systolic_bp_points, 150.0, 40.0, 300.0),
    (pulse_points, 70.0, 20.0, 220.0),
    (temperature_points, 37.0, 30.0, 43.0),
]


@pytest.mark.parametrize(("function", "normal", "low", "high"), MONOTONE_CASES)
@given(fraction_a=st.floats(0, 1), fraction_b=st.floats(0, 1))
def test_points_never_fall_as_a_value_moves_away_from_normal(
    function: Band, normal: float, low: float, high: float, fraction_a: float, fraction_b: float
) -> None:
    near, far = sorted((fraction_a, fraction_b))
    above = (normal + near * (high - normal), normal + far * (high - normal))
    below = (normal - near * (normal - low), normal - far * (normal - low))
    assert one(function, above[1]) >= one(function, above[0])
    assert one(function, below[1]) >= one(function, below[0])
