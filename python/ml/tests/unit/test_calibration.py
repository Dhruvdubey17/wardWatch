import math

import numpy as np
import pytest
from wardwatch_ml.calibration import (
    IsotonicCalibrator,
    PlattCalibrator,
    brier_score,
    calibrator_from_dict,
    expected_calibration_error,
    reliability,
    sigmoid,
)

pytestmark = pytest.mark.unit


def test_brier_hand_case() -> None:
    # ((1 - 0.8)^2 + (0 - 0.3)^2) / 2 = (0.04 + 0.09) / 2 = 0.065
    assert brier_score([1, 0], [0.8, 0.3]) == pytest.approx(0.065)


def test_reliability_bins_and_ece_hand_case() -> None:
    labels = [0, 0, 1, 1, 1, 0]
    probability = [0.05, 0.15, 0.95, 0.85, 1.0, 0.95]
    table = reliability(labels, probability, bins=10)
    assert [entry.count for entry in table] == [1, 1, 0, 0, 0, 0, 0, 0, 1, 3]
    assert table[9].mean_predicted == pytest.approx((0.95 + 1.0 + 0.95) / 3)
    assert table[9].observed_rate == pytest.approx(2 / 3)
    assert math.isnan(table[4].mean_predicted)
    # Gaps: bin0 |0.05-0| = 0.05, bin1 0.15, bin8 |0.85-1| = 0.15,
    # bin9 |0.9667-0.6667| = 0.30. Weighted: (0.05 + 0.15 + 0.15 + 3 * 0.30) / 6.
    assert expected_calibration_error(labels, probability, bins=10) == pytest.approx(
        (0.05 + 0.15 + 0.15 + 0.9) / 6
    )


def test_perfectly_calibrated_predictions_have_small_ece() -> None:
    random = np.random.default_rng(2019)
    probability = random.uniform(size=50_000)
    labels = random.uniform(size=50_000) < probability
    assert expected_calibration_error(labels, probability) < 0.01


def test_platt_recovers_a_known_logistic_relationship() -> None:
    random = np.random.default_rng(1)
    margin = random.normal(size=40_000)
    labels = random.uniform(size=margin.size) < sigmoid(2.0 * margin - 1.0)
    calibrator = PlattCalibrator.fit(margin, labels)
    assert calibrator.slope == pytest.approx(2.0, abs=0.08)
    assert calibrator.intercept == pytest.approx(-1.0, abs=0.05)
    assert calibrator.probability([0.5])[0] == pytest.approx(0.5, abs=0.02)


def test_isotonic_is_monotone_and_clipped() -> None:
    margin = np.array([-2.0, -1.0, 0.0, 1.0, 2.0, 3.0])
    labels = np.array([0, 0, 1, 0, 1, 1])
    calibrator = IsotonicCalibrator.fit(margin, labels)
    probability = calibrator.probability(np.linspace(-5, 5, 101))
    assert np.all(np.diff(probability) >= 0)
    assert calibrator.probability([-10.0])[0] == pytest.approx(0.0)
    assert calibrator.probability([10.0])[0] == pytest.approx(1.0)
    # The pool-adjacent-violators fit averages the 1 at 0.0 and 0 at 1.0 to 0.5.
    assert calibrator.probability([0.0])[0] == pytest.approx(0.5)


def test_calibration_lowers_brier_on_overconfident_scores() -> None:
    random = np.random.default_rng(3)
    margin = random.normal(size=20_000)
    labels = random.uniform(size=margin.size) < sigmoid(0.5 * margin)
    raw = sigmoid(3.0 * margin)
    for calibrator in (PlattCalibrator.fit(margin, labels), IsotonicCalibrator.fit(margin, labels)):
        assert brier_score(labels, calibrator.probability(margin)) < brier_score(labels, raw)


@pytest.mark.parametrize(
    "calibrator",
    [PlattCalibrator(1.5, -0.2), IsotonicCalibrator((-1.0, 0.0, 1.0), (0.1, 0.4, 0.9))],
)
def test_calibrators_round_trip_through_dicts(
    calibrator: PlattCalibrator | IsotonicCalibrator,
) -> None:
    rebuilt = calibrator_from_dict(calibrator.to_dict())
    assert rebuilt == calibrator
    np.testing.assert_array_equal(rebuilt.probability([0.3]), calibrator.probability([0.3]))


def test_unknown_calibrator_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown calibrator"):
        calibrator_from_dict({"kind": "beta"})
