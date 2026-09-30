from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from wardwatch_ml.data import LABEL, load_site
from wardwatch_ml.features import FeatureSpec, build_features
from wardwatch_ml.onset import stay_outcomes
from wardwatch_ml.splits import calibration_split, patient_folds, rows_for
from wardwatch_ml.xgb_model import XgbParams, predict_margin, train_xgboost

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "physionet"
SMOKE = XgbParams(max_rounds=20, early_stopping_rounds=5, threads=1)


@pytest.fixture(scope="module")
def site_a() -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = load_site(FIXTURES / "training_setA", "A")
    features = build_features(frame)
    features[LABEL] = frame[LABEL].to_numpy()
    return features, stay_outcomes(frame)


def test_folds_never_share_a_patient(site_a: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    _, outcomes = site_a
    folds = patient_folds(outcomes, folds=3, seed=1)
    everyone: set[tuple[str, str]] = set()
    for fold in folds:
        assert not fold.train & fold.valid
        assert len(fold.train | fold.valid) == 9
        everyone |= fold.valid
    assert len(everyone) == 9


def test_every_fold_sees_septic_stays(site_a: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    _, outcomes = site_a
    septic = {
        (site, patient)
        for site, patient, is_septic in outcomes[["site", "patient_id", "septic"]].itertuples(
            index=False
        )
        if is_septic
    }
    for fold in patient_folds(outcomes, folds=3, seed=1):
        assert fold.valid & septic
        assert fold.train & septic


def test_calibration_split_is_disjoint_and_stratified(
    site_a: tuple[pd.DataFrame, pd.DataFrame],
) -> None:
    _, outcomes = site_a
    fit, held_out = calibration_split(outcomes, fraction=0.34, seed=3)
    assert not fit & held_out
    assert len(fit) + len(held_out) == 9
    assert len(held_out) == 4


def test_rows_for_selects_whole_stays(site_a: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    features, _ = site_a
    mask = rows_for(features, frozenset({("A", "p000001")}))
    assert int(mask.sum()) == 40
    assert set(features.loc[mask, "patient_id"]) == {"p000001"}


def test_smoke_fit_produces_out_of_fold_scores(site_a: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    features, outcomes = site_a
    names = FeatureSpec().feature_names()
    result = train_xgboost(features, names, patient_folds(outcomes, 3, seed=1), SMOKE)
    # Every row is scored exactly once, by the fold that held its stay out.
    assert not result.oof_margin.isna().any()
    assert len(result.best_iterations) == 3
    assert result.final_rounds >= 1
    margins = predict_margin(result.booster, features, names)
    assert margins.shape == (len(features),)
    septic_rows = features[LABEL].to_numpy() == 1
    # The fixtures' septic stays deteriorate clearly, so even a tiny model ranks them higher.
    assert margins[septic_rows].mean() > margins[~septic_rows].mean()


def test_training_is_deterministic(site_a: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    features, outcomes = site_a
    names = FeatureSpec().feature_names()
    folds = patient_folds(outcomes, 3, seed=1)
    first = train_xgboost(features, names, folds, SMOKE)
    second = train_xgboost(features, names, folds, SMOKE)
    np.testing.assert_array_equal(first.oof_margin.to_numpy(), second.oof_margin.to_numpy())
    np.testing.assert_array_equal(
        predict_margin(first.booster, features, names),
        predict_margin(second.booster, features, names),
    )
