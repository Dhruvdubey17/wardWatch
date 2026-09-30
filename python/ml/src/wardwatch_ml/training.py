"""Fits everything that comes from the training site: models, calibrators, thresholds.

The same function backs `make train` (the serving bundle) and each direction
of `make eval`, so the served model is fitted exactly as it was evaluated.
"""

import logging
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from wardwatch_ml.calibration import IsotonicCalibrator, PlattCalibrator
from wardwatch_ml.data import LABEL
from wardwatch_ml.features import FeatureSpec, build_features
from wardwatch_ml.fingerprint import DataFingerprint, data_fingerprint
from wardwatch_ml.gru_model import (
    GruParams,
    GruTrainingResult,
    predict_logits,
    to_sequences,
    train_gru,
)
from wardwatch_ml.metrics import add_alerts, event_metrics
from wardwatch_ml.onset import stay_outcomes
from wardwatch_ml.splits import calibration_split, patient_folds, rows_for
from wardwatch_ml.thresholds import (
    OperatingPoint,
    choose_fixed_sensitivity,
    choose_matched_burden,
    news2_policy,
)
from wardwatch_ml.xgb_model import XgbParams, XgbTrainingResult, predict_margin, train_xgboost

log = logging.getLogger(__name__)

# The rise that breaks through the refractory period, in log-odds: roughly a
# 2.7-fold increase in the odds of sepsis since the last alert.
MODEL_RISE_MARGIN = 1.0


@dataclass(frozen=True)
class TrainingConfig:
    folds: int = 5
    calibration_fraction: float = 0.2
    seed: int = 2019
    xgb: XgbParams = field(default_factory=XgbParams)
    gru: GruParams = field(default_factory=GruParams)
    train_gru: bool = True


@dataclass
class FittedModel:
    name: str
    calibrators: dict[str, PlattCalibrator | IsotonicCalibrator]
    operating_points: dict[str, OperatingPoint]


@dataclass
class FittedSite:
    site: str
    spec: FeatureSpec
    xgb: XgbTrainingResult
    gru: GruTrainingResult | None
    models: dict[str, FittedModel]
    news2_target_alerts_per_day: float
    # What each fitted artifact saw. Every entry must name only the training site.
    fingerprints: dict[str, DataFingerprint]
    timings_seconds: dict[str, float]


def labelled_features(frame: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    features = build_features(frame, spec)
    features[LABEL] = frame[LABEL].to_numpy()
    return features


def _fit_calibrators(
    margin: np.ndarray, labels: np.ndarray
) -> dict[str, PlattCalibrator | IsotonicCalibrator]:
    if labels.min() == labels.max():
        # A calibration set with one class cannot be fitted; keep the raw
        # sigmoid so the pipeline still runs (only happens on tiny fixtures).
        log.warning("calibration set has a single class; using identity calibrators")
        return {
            "platt": PlattCalibrator(slope=1.0, intercept=0.0),
            "isotonic": IsotonicCalibrator(knots_x=(-50.0, 50.0), knots_y=(0.0, 1.0)),
        }
    return {
        "platt": PlattCalibrator.fit(margin, labels),
        "isotonic": IsotonicCalibrator.fit(margin, labels),
    }


def fit_site(
    frame: pd.DataFrame, config: TrainingConfig, spec: FeatureSpec | None = None
) -> FittedSite:
    """Fit on one site's long table (one site only)."""
    sites = frame["site"].unique()
    if len(sites) != 1:
        raise ValueError(f"fit_site takes one site, got {sorted(map(str, sites))}")
    site = str(sites[0])
    spec = spec or FeatureSpec()
    names = spec.feature_names()
    timings: dict[str, float] = {}

    started = time.monotonic()
    features = labelled_features(frame, spec)
    outcomes = stay_outcomes(frame)
    fit_stays, calibration_stays = calibration_split(
        outcomes, config.calibration_fraction, config.seed
    )
    fit_mask = rows_for(features, fit_stays).to_numpy()
    calibration_mask = rows_for(features, calibration_stays).to_numpy()
    fit_outcomes = outcomes[rows_for(outcomes, fit_stays).to_numpy()]
    calibration_outcomes = outcomes[rows_for(outcomes, calibration_stays).to_numpy()]
    timings["features"] = time.monotonic() - started

    started = time.monotonic()
    folds = patient_folds(fit_outcomes, config.folds, config.seed)
    xgb_result = train_xgboost(features[fit_mask], names, folds, config.xgb)
    timings["xgboost"] = time.monotonic() - started
    log.info("xgboost on site %s: %d rounds", site, xgb_result.final_rounds)

    calibration_rows = features[calibration_mask]
    calibration_labels = calibration_rows[LABEL].to_numpy()
    scored = {
        "xgboost": calibration_rows[["site", "patient_id", "hour"]].assign(
            score=predict_margin(xgb_result.booster, calibration_rows, names)
        )
    }

    gru_result = None
    if config.train_gru:
        started = time.monotonic()
        validation = folds[0].valid
        fit_rows = features[fit_mask]
        gru_train = fit_rows[~rows_for(fit_rows, validation).to_numpy()]
        gru_valid = fit_rows[rows_for(fit_rows, validation).to_numpy()]
        gru_result = train_gru(gru_train, gru_valid, spec, config.gru)
        timings["gru"] = time.monotonic() - started
        sequences = to_sequences(calibration_rows, gru_result.scaler)
        scored["gru"] = calibration_rows[["site", "patient_id", "hour"]].assign(
            score=predict_logits(gru_result.model, sequences, len(calibration_rows))
        )

    started = time.monotonic()
    all_rows = features[["site", "patient_id", "hour"]].assign(score=features["news2_total"])
    news2_target = event_metrics(
        all_rows, add_alerts(all_rows, news2_policy()), outcomes
    ).alerts_per_patient_day
    models: dict[str, FittedModel] = {}
    for name, table in scored.items():
        margin = table["score"].to_numpy()
        models[name] = FittedModel(
            name=name,
            calibrators=_fit_calibrators(margin, calibration_labels),
            operating_points={
                "matched_burden": choose_matched_burden(
                    table, calibration_outcomes, news2_target, MODEL_RISE_MARGIN
                ),
                "fixed_sensitivity": choose_fixed_sensitivity(
                    table, calibration_outcomes, MODEL_RISE_MARGIN
                ),
            },
        )
    timings["thresholds"] = time.monotonic() - started

    fingerprints = {
        "xgboost": data_fingerprint(frame[rows_for(frame, fit_stays).to_numpy()]),
        "calibration_and_thresholds": data_fingerprint(
            frame[rows_for(frame, calibration_stays).to_numpy()]
        ),
        "news2_target": data_fingerprint(frame),
    }
    if gru_result is not None:
        fingerprints["gru"] = fingerprints["xgboost"]
    return FittedSite(
        site=site,
        spec=spec,
        xgb=xgb_result,
        gru=gru_result,
        models=models,
        news2_target_alerts_per_day=news2_target,
        fingerprints=fingerprints,
        timings_seconds=timings,
    )
