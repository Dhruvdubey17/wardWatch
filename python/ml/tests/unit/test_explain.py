from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xgboost as xgb
from wardwatch_ml.data import LABEL, load_site
from wardwatch_ml.explain import Explainer
from wardwatch_ml.features import FeatureSpec, build_features
from wardwatch_ml.labels import feature_label
from wardwatch_ml.onset import stay_outcomes
from wardwatch_ml.splits import patient_folds
from wardwatch_ml.xgb_model import XgbParams, predict_margin, train_xgboost

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "physionet"
NAMES = FeatureSpec().feature_names()


@pytest.fixture(scope="module")
def trained() -> tuple[pd.DataFrame, Explainer, xgb.Booster]:
    frame = load_site(FIXTURES / "training_setA", "A")
    features = build_features(frame)
    features[LABEL] = frame[LABEL].to_numpy()
    params = XgbParams(max_rounds=30, early_stopping_rounds=10, threads=1)
    result = train_xgboost(features, NAMES, patient_folds(stay_outcomes(frame), 3, seed=1), params)
    return features, Explainer(result.booster, NAMES), result.booster


def test_contributions_plus_base_equal_the_margin(
    trained: tuple[pd.DataFrame, Explainer, xgb.Booster],
) -> None:
    features, explainer, booster = trained
    contributions = explainer.contributions(features)
    margin = predict_margin(booster, features, NAMES)
    np.testing.assert_allclose(contributions.sum(axis=1) + explainer.base_value, margin, atol=1e-4)


def test_top_factors_push_risk_up_in_order(
    trained: tuple[pd.DataFrame, Explainer, xgb.Booster],
) -> None:
    features, explainer, _ = trained
    septic_row = features[features[LABEL] == 1].iloc[[-1]]
    (factors,) = explainer.top_factors(septic_row)
    assert 1 <= len(factors) <= 5
    contributions = [factor.contribution for factor in factors]
    assert contributions == sorted(contributions, reverse=True)
    assert all(contribution > 0 for contribution in contributions)
    for factor in factors:
        assert factor.label == feature_label(factor.feature).label
        expected = septic_row[factor.feature].iloc[0]
        assert (
            factor.value is None if np.isnan(expected) else factor.value == pytest.approx(expected)
        )


def test_every_feature_has_a_label() -> None:
    labels = {name: feature_label(name) for name in NAMES}
    assert len({label.label for label in labels.values()}) == len(NAMES)
    assert all(label.label for label in labels.values())


@pytest.mark.parametrize(
    ("feature", "label", "unit"),
    [
        ("Resp_delta_6h", "Respiratory rate, change over 6 h", "/min"),
        ("Lactate_last", "Lactate, latest", "mmol/L"),
        ("SBP_min_6h", "Systolic blood pressure, lowest over 6 h", "mmHg"),
        ("Temp_max_12h", "Temperature, highest over 12 h", "°C"),
        ("HR_mean_12h", "Heart rate, average over 12 h", "/min"),
        ("WBC_measured", "White cell count, measured this hour", ""),
        ("Creatinine_hours_since", "Creatinine, hours since last measured", "h"),
        ("news2_supplemental_o2", "NEWS2 supplemental oxygen points", "points"),
        ("news2_total", "NEWS2 total", "points"),
        ("icu_hour", "Hours since ICU admission", "h"),
    ],
)
def test_label_examples(feature: str, label: str, unit: str) -> None:
    assert feature_label(feature).label == label
    assert feature_label(feature).unit == unit


def test_unknown_feature_raises() -> None:
    with pytest.raises(KeyError, match="no clinician label"):
        feature_label("Glucose_last")
