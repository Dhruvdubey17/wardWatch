"""Per-hour SHAP explanations for the XGBoost model.

Contributions are in the model's margin (log-odds) space: for any row, the
contributions plus the explainer's base value equal the margin prediction.
"""

from dataclasses import asdict, dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd
import shap
import xgboost as xgb

from wardwatch_ml.labels import feature_label

TOP_FACTORS = 5


@dataclass(frozen=True)
class Factor:
    feature: str
    label: str
    unit: str
    value: float | None
    contribution: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class Explainer:
    def __init__(self, booster: xgb.Booster, feature_names: list[str]) -> None:
        self.feature_names = feature_names
        self._explainer = shap.TreeExplainer(booster, feature_perturbation="tree_path_dependent")

    @property
    def base_value(self) -> float:
        return float(np.asarray(self._explainer.expected_value).reshape(-1)[0])

    def contributions(self, features: pd.DataFrame) -> npt.NDArray[np.float64]:
        matrix = features[self.feature_names].to_numpy(dtype=np.float32)
        values = self._explainer.shap_values(matrix)
        return np.asarray(values, dtype=np.float64)

    def top_factors(self, features: pd.DataFrame, k: int = TOP_FACTORS) -> list[list[Factor]]:
        """For each row, the k features that pushed risk up the most."""
        contributions = self.contributions(features)
        values = features[self.feature_names].to_numpy(dtype=np.float64)
        result = []
        for row_contributions, row_values in zip(contributions, values, strict=True):
            order = np.argsort(-row_contributions, kind="stable")
            factors = []
            for index in order[:k]:
                if row_contributions[index] <= 0:
                    break
                name = self.feature_names[index]
                label = feature_label(name)
                value = row_values[index]
                factors.append(
                    Factor(
                        feature=name,
                        label=label.label,
                        unit=label.unit,
                        value=None if np.isnan(value) else float(value),
                        contribution=float(row_contributions[index]),
                    )
                )
            result.append(factors)
        return result
