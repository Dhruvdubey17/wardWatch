"""XGBoost on the tabular features, with patient-level cross-validation.

Each fold trains with early stopping on its validation stays; the out-of-fold
margins become the training-site scores used to pick thresholds, so no stay
is scored by a model that saw it. The final model trains on every fit stay
for the median best iteration count. Positive hours are weighted by the
negative to positive ratio of the stays being trained on.
"""

from dataclasses import asdict, dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd
import xgboost as xgb

from wardwatch_ml.data import LABEL
from wardwatch_ml.splits import Fold, rows_for


@dataclass(frozen=True)
class XgbParams:
    max_depth: int = 4
    learning_rate: float = 0.05
    max_rounds: int = 2000
    early_stopping_rounds: int = 50
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    min_child_weight: float = 5.0
    reg_lambda: float = 1.0
    threads: int = 4
    seed: int = 2019

    def booster_params(self, scale_pos_weight: float) -> dict[str, object]:
        return {
            "objective": "binary:logistic",
            "eval_metric": "aucpr",
            "tree_method": "hist",
            "max_depth": self.max_depth,
            "eta": self.learning_rate,
            "subsample": self.subsample,
            "colsample_bytree": self.colsample_bytree,
            "min_child_weight": self.min_child_weight,
            "lambda": self.reg_lambda,
            "scale_pos_weight": scale_pos_weight,
            "nthread": self.threads,
            "seed": self.seed,
        }


@dataclass
class XgbTrainingResult:
    booster: xgb.Booster
    feature_names: list[str]
    best_iterations: list[int]
    final_rounds: int
    # Out-of-fold margins (log-odds) for every fit row, indexed like the input.
    oof_margin: pd.Series
    params: XgbParams

    def summary(self) -> dict[str, object]:
        return {
            "best_iterations": self.best_iterations,
            "final_rounds": self.final_rounds,
            "params": asdict(self.params),
        }


def _matrix(features: pd.DataFrame, feature_names: list[str]) -> npt.NDArray[np.float32]:
    return features[feature_names].to_numpy(dtype=np.float32)


def _scale_pos_weight(labels: npt.NDArray[np.int64]) -> float:
    positives = int(labels.sum())
    negatives = len(labels) - positives
    return negatives / positives if positives else 1.0


def train_xgboost(
    features: pd.DataFrame,
    feature_names: list[str],
    folds: list[Fold],
    params: XgbParams,
) -> XgbTrainingResult:
    """Cross-validate over `folds`, then fit the final model on all their stays.

    `features` holds the fit stays' rows with site, patient_id, the feature
    columns and SepsisLabel.
    """
    labels = features[LABEL].to_numpy().astype(np.int64)
    matrix = _matrix(features, feature_names)
    oof = np.full(len(features), np.nan)
    best_iterations = []
    for fold in folds:
        train_rows = rows_for(features, fold.train).to_numpy()
        valid_rows = rows_for(features, fold.valid).to_numpy()
        train = xgb.DMatrix(
            matrix[train_rows], label=labels[train_rows], feature_names=feature_names
        )
        valid = xgb.DMatrix(
            matrix[valid_rows], label=labels[valid_rows], feature_names=feature_names
        )
        booster = xgb.train(
            params.booster_params(_scale_pos_weight(labels[train_rows])),
            train,
            num_boost_round=params.max_rounds,
            evals=[(valid, "valid")],
            early_stopping_rounds=params.early_stopping_rounds,
            verbose_eval=False,
        )
        best_iterations.append(int(booster.best_iteration))
        oof[valid_rows] = booster.predict(
            valid, output_margin=True, iteration_range=(0, booster.best_iteration + 1)
        )
    final_rounds = int(np.median(best_iterations)) + 1
    everything = xgb.DMatrix(matrix, label=labels, feature_names=feature_names)
    final = xgb.train(
        params.booster_params(_scale_pos_weight(labels)),
        everything,
        num_boost_round=final_rounds,
        verbose_eval=False,
    )
    return XgbTrainingResult(
        booster=final,
        feature_names=feature_names,
        best_iterations=best_iterations,
        final_rounds=final_rounds,
        oof_margin=pd.Series(oof, index=features.index),
        params=params,
    )


def predict_margin(
    booster: xgb.Booster, features: pd.DataFrame, feature_names: list[str]
) -> npt.NDArray[np.float64]:
    """Log-odds scores; the alert policy and calibrators work on this scale."""
    matrix = xgb.DMatrix(_matrix(features, feature_names), feature_names=feature_names)
    return np.asarray(booster.predict(matrix, output_margin=True), dtype=np.float64)
