import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xgboost as xgb
from wardwatch_ml.bundle import ServingBundle, model_version, write_bundle
from wardwatch_ml.data import load_site
from wardwatch_ml.gru_model import GruParams
from wardwatch_ml.training import FittedSite, TrainingConfig, fit_site, labelled_features
from wardwatch_ml.xgb_model import XgbParams

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "physionet"
SMOKE = TrainingConfig(
    folds=2,
    calibration_fraction=0.34,
    xgb=XgbParams(max_rounds=30, early_stopping_rounds=10, threads=1),
    gru=GruParams(hidden_size=8, epochs=1, max_steps=2, threads=1),
    train_gru=False,
)


@pytest.fixture(scope="module")
def fitted() -> tuple[FittedSite, pd.DataFrame]:
    frame = load_site(FIXTURES / "training_setA", "A")
    return fit_site(frame, SMOKE), frame


def test_bundle_round_trip_gives_identical_scores(
    fitted: tuple[FittedSite, pd.DataFrame], tmp_path: Path
) -> None:
    site, frame = fitted
    directory = write_bundle(site, tmp_path, "xgb-test-0000000", "0" * 40)
    bundle = ServingBundle.load(directory)
    features = labelled_features(frame, site.spec)
    names = site.spec.feature_names()
    matrix = xgb.DMatrix(features[names].to_numpy(np.float32), feature_names=names)
    original = site.xgb.booster.predict(matrix, output_margin=True)
    np.testing.assert_array_equal(bundle.margin(features), original)
    assert bundle.spec == site.spec
    assert bundle.calibrator == site.models["xgboost"].calibrators["platt"]
    assert bundle.policy == site.models["xgboost"].operating_points["matched_burden"].policy
    assert bundle.news2_policy.threshold == 5.0
    probability = bundle.probability(features)
    assert ((probability >= 0) & (probability <= 1)).all()


def test_manifest_records_provenance(
    fitted: tuple[FittedSite, pd.DataFrame], tmp_path: Path
) -> None:
    site, _ = fitted
    directory = write_bundle(site, tmp_path, "xgb-test-0000001", "a" * 40)
    manifest = json.loads((directory / "bundle.json").read_text())
    assert manifest["git_sha"] == "a" * 40
    assert manifest["train_site"] == "A"
    assert manifest["data_fingerprint"]["sites"] == ["A"]
    assert set(manifest["operating_points"]) == {"matched_burden", "fixed_sensitivity"}
    assert manifest["served"] == {
        "model": "xgboost",
        "calibrator": "platt",
        "operating_point": "matched_burden",
    }
    assert len(manifest["feature_names"]) == 164


def test_explanations_come_from_the_loaded_model(
    fitted: tuple[FittedSite, pd.DataFrame], tmp_path: Path
) -> None:
    site, frame = fitted
    bundle = ServingBundle.load(write_bundle(site, tmp_path, "xgb-test-0000002", "b" * 40))
    features = labelled_features(frame, site.spec).tail(3)
    factors = bundle.explain(features)
    assert len(factors) == 3
    assert all(len(row) <= 5 for row in factors)


def test_existing_version_is_not_overwritten(
    fitted: tuple[FittedSite, pd.DataFrame], tmp_path: Path
) -> None:
    site, _ = fitted
    write_bundle(site, tmp_path, "xgb-same", "c" * 40)
    with pytest.raises(FileExistsError):
        write_bundle(site, tmp_path, "xgb-same", "c" * 40)


def test_unknown_bundle_format_is_rejected(
    fitted: tuple[FittedSite, pd.DataFrame], tmp_path: Path
) -> None:
    site, _ = fitted
    directory = write_bundle(site, tmp_path, "xgb-old", "d" * 40)
    manifest = json.loads((directory / "bundle.json").read_text())
    manifest["format"] = 99
    (directory / "bundle.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="bundle format 99"):
        ServingBundle.load(directory)


def test_model_version_embeds_the_commit() -> None:
    assert model_version("0123456789abcdef").endswith("-0123456")
    assert model_version("0123456789abcdef").startswith("xgb-")
