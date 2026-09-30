"""Scores a test site with models fitted on the other site and measures the results.

Nothing here fits anything: thresholds, calibrators and models arrive frozen
from wardwatch_ml.training, and a check at the end confirms they are unchanged.
"""

import copy
import logging
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import precision_recall_curve, roc_curve

from wardwatch_ml.calibration import brier_score, expected_calibration_error, reliability, sigmoid
from wardwatch_ml.data import LABEL
from wardwatch_ml.gru_model import predict_logits, to_sequences
from wardwatch_ml.metrics import (
    RankedScores,
    add_alerts,
    bootstrap_interval,
    bootstrap_weights,
    normalized_from_components,
    stay_summary,
    summary_metrics,
    utility_components,
)
from wardwatch_ml.onset import stay_outcomes
from wardwatch_ml.policy import AlertPolicy
from wardwatch_ml.thresholds import OperatingPoint, news2_policy
from wardwatch_ml.training import FittedSite, labelled_features
from wardwatch_ml.xgb_model import predict_margin

log = logging.getLogger(__name__)


def score_site(fitted: FittedSite, features: pd.DataFrame) -> dict[str, np.ndarray]:
    """Scores on the policy's scale: model margins, and the NEWS2 total."""
    names = fitted.spec.feature_names()
    scores = {"xgboost": predict_margin(fitted.xgb.booster, features, names)}
    if fitted.gru is not None:
        sequences = to_sequences(features, fitted.gru.scaler)
        scores["gru"] = predict_logits(fitted.gru.model, sequences, len(features))
    scores["news2"] = features["news2_total"].to_numpy(dtype=np.float64)
    return scores


def _interval(weights: np.ndarray, statistic: Callable[[np.ndarray], float]) -> list[float]:
    low, high = bootstrap_interval(weights, statistic)
    return [low, high]


class SiteEvaluation:
    """Shared per-stay structures for one test site, built once and reused per model."""

    def __init__(
        self, frame: pd.DataFrame, features: pd.DataFrame, resamples: int, seed: int
    ) -> None:
        self.features = features
        self.outcomes = stay_outcomes(frame)
        keys = list(
            zip(features["site"].astype(str), features["patient_id"].astype(str), strict=True)
        )
        self.stays = sorted(set(keys))
        position = {key: index for index, key in enumerate(self.stays)}
        self.stay_index = np.array([position[key] for key in keys], dtype=np.int64)
        self.labels = features[LABEL].to_numpy(dtype=np.int64)
        self.weights = bootstrap_weights(len(self.stays), resamples, seed)
        self._rows_by_stay = (
            pd.Series(np.arange(len(features))).groupby(self.stay_index).apply(np.asarray)
        )

    def keys_frame(self, scores: np.ndarray) -> pd.DataFrame:
        return self.features[["site", "patient_id", "hour"]].assign(score=scores)

    def ordered_summary(self, scores: np.ndarray, policy: AlertPolicy) -> pd.DataFrame:
        scored = self.keys_frame(scores)
        summary = stay_summary(scored, add_alerts(scored, policy), self.outcomes)
        order = {key: index for index, key in enumerate(self.stays)}
        summary["order"] = [
            order[(s, p)] for s, p in zip(summary["site"], summary["patient_id"], strict=True)
        ]
        return summary.sort_values("order").reset_index(drop=True)

    def utility(self, scores: np.ndarray, threshold: float) -> np.ndarray:
        predictions = scores >= threshold
        return utility_components(
            [(self.labels[rows], predictions[rows]) for rows in self._rows_by_stay]
        )


def _event_block(
    evaluation: SiteEvaluation, scores: np.ndarray, point: OperatingPoint
) -> dict[str, Any]:
    summary = evaluation.ordered_summary(scores, point.policy)
    metrics = summary_metrics(summary)
    weights = evaluation.weights
    components = evaluation.utility(scores, point.policy.threshold)
    return {
        "operating_point": point.to_dict(),
        "events": metrics.to_dict(),
        "utility": normalized_from_components(components),
        "ci95": {
            "utility": _interval(weights, lambda w: normalized_from_components(components, w)),
            "event_sensitivity": _interval(
                weights, lambda w: summary_metrics(summary, w).event_sensitivity
            ),
            "median_lead_time_hours": _interval(
                weights, lambda w: summary_metrics(summary, w).median_lead_time_hours
            ),
            "alerts_per_patient_day": _interval(
                weights, lambda w: summary_metrics(summary, w).alerts_per_patient_day
            ),
            "false_alerts_per_patient_day": _interval(
                weights, lambda w: summary_metrics(summary, w).false_alerts_per_patient_day
            ),
            "ppv_per_alert": _interval(
                weights, lambda w: summary_metrics(summary, w).ppv_per_alert
            ),
        },
    }


def _calibration_block(
    evaluation: SiteEvaluation, margin: np.ndarray, fitted_model: Any
) -> dict[str, Any]:
    labels = evaluation.labels
    variants = {"uncalibrated": sigmoid(margin)}
    for name, calibrator in fitted_model.calibrators.items():
        variants[name] = calibrator.probability(margin)
    return {
        name: {
            "brier": brier_score(labels, probability),
            "ece": expected_calibration_error(labels, probability),
            "reliability": [asdict(entry) for entry in reliability(labels, probability)],
        }
        for name, probability in variants.items()
    }


def evaluate_site(
    fitted: FittedSite, test_frame: pd.DataFrame, resamples: int, seed: int
) -> dict[str, Any]:
    """Every metric for every model on one test site."""
    test_sites = set(test_frame["site"].astype(str))
    if fitted.site in test_sites:
        raise ValueError("the test site must differ from the training site")
    frozen = copy.deepcopy({name: model.operating_points for name, model in fitted.models.items()})

    features = labelled_features(test_frame, fitted.spec)
    evaluation = SiteEvaluation(test_frame, features, resamples, seed)
    scores = score_site(fitted, features)
    results: dict[str, Any] = {}
    for name, values in scores.items():
        ranked = RankedScores(evaluation.labels, values, evaluation.stay_index)
        block: dict[str, Any] = {
            "auroc": ranked.auroc(),
            "auprc": ranked.auprc(),
            "ci95": {
                "auroc": _interval(evaluation.weights, ranked.auroc),
                "auprc": _interval(evaluation.weights, ranked.auprc),
            },
        }
        if name == "news2":
            points = {
                "news2_urgent": OperatingPoint("news2_urgent", news2_policy(), 5.0, float("nan"))
            }
        else:
            points = fitted.models[name].operating_points
            block["calibration"] = _calibration_block(evaluation, values, fitted.models[name])
        block["operating_points"] = {
            point_name: _event_block(evaluation, values, point)
            for point_name, point in points.items()
        }
        results[name] = block
        log.info(
            "scored %s on site %s: AUROC %.3f", name, ",".join(sorted(test_sites)), block["auroc"]
        )

    unchanged = frozen == {name: model.operating_points for name, model in fitted.models.items()}
    return {
        "test_site": ",".join(sorted(test_sites)),
        "test_stays": len(evaluation.stays),
        "test_hours": len(features),
        "unknown_onset_septic_stays": int(
            (evaluation.outcomes["septic"] & ~evaluation.outcomes["onset_known"]).sum()
        ),
        "results": results,
        "thresholds_unchanged_by_test_site": unchanged,
        "curves": _curves(evaluation, scores, fitted),
    }


def _downsample(x: np.ndarray, y: np.ndarray, points: int = 200) -> dict[str, list[float]]:
    index = np.unique(np.linspace(0, len(x) - 1, min(points, len(x))).round().astype(int))
    return {"x": [float(v) for v in x[index]], "y": [float(v) for v in y[index]]}


def _curves(
    evaluation: SiteEvaluation, scores: dict[str, np.ndarray], fitted: FittedSite
) -> dict[str, Any]:
    """ROC, PR and burden-against-sensitivity curves, for the plots only."""
    curves: dict[str, Any] = {"roc": {}, "pr": {}, "burden": {}}
    for name, values in scores.items():
        fpr, tpr, _ = roc_curve(evaluation.labels, values)
        precision, recall, _ = precision_recall_curve(evaluation.labels, values)
        curves["roc"][name] = _downsample(fpr, tpr)
        curves["pr"][name] = _downsample(recall[::-1], precision[::-1])
        margin = (
            fitted.models[name].operating_points["matched_burden"].policy.rise_margin
            if name in fitted.models
            else 2.0
        )
        points = []
        for threshold in np.unique(np.quantile(values, np.linspace(0.5, 0.999, 25))):
            policy = AlertPolicy(float(threshold), 6.0, margin)
            metrics = summary_metrics(evaluation.ordered_summary(values, policy))
            points.append(
                {
                    "threshold": float(threshold),
                    "event_sensitivity": metrics.event_sensitivity,
                    "alerts_per_patient_day": metrics.alerts_per_patient_day,
                }
            )
        curves["burden"][name] = points
    return curves
