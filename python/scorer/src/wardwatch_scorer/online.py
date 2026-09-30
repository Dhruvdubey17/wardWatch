"""Scores one closed ICU hour with the same feature code the offline pipeline uses."""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from wardwatch_ml.bundle import ServingBundle
from wardwatch_ml.explain import Factor
from wardwatch_ml.features import FeatureSpec, build_features
from wardwatch_ml.news2 import COMPONENTS

from wardwatch_scorer.window import EncounterWindow

NEWS2_ONLY_VERSION = "news2-only"
NEWS2_VERSION = "news2-rcp-2017"


@dataclass(frozen=True)
class HourScore:
    icu_hour: int
    features: pd.DataFrame
    news2_components: dict[str, int]
    news2_total: int
    raw_score: float | None
    calibrated_probability: float | None

    @property
    def single_parameter_three(self) -> bool:
        return any(points == 3 for points in self.news2_components.values())

    def news2_payload(self) -> dict[str, object]:
        return {
            "total": self.news2_total,
            "components": self.news2_components,
            "single_parameter_three": self.single_parameter_three,
        }


class OnlineScorer:
    def __init__(self, bundle: ServingBundle | None, spec: FeatureSpec | None = None) -> None:
        self.bundle = bundle
        self.spec = bundle.spec if bundle is not None else (spec or FeatureSpec())

    @property
    def model_version(self) -> str:
        return self.bundle.model_version if self.bundle is not None else NEWS2_ONLY_VERSION

    def score(self, window: EncounterWindow, hour: int) -> HourScore:
        rows = window.rows(hour, self.spec.history_hours)
        features = build_features(rows, self.spec).iloc[[-1]].reset_index(drop=True)
        components = {
            name: 0 if name == "consciousness" else int(features[f"news2_{name}"].iloc[0])
            for name in COMPONENTS
        }
        raw = probability = None
        if self.bundle is not None:
            margin = self.bundle.margin(features)
            raw = float(margin[0])
            probability = float(np.clip(self.bundle.calibrator.probability(margin)[0], 0.0, 1.0))
        return HourScore(
            icu_hour=hour,
            features=features,
            news2_components=components,
            news2_total=int(features["news2_total"].iloc[0]),
            raw_score=raw,
            calibrated_probability=probability,
        )

    def explain(self, score: HourScore) -> list[Factor]:
        if self.bundle is None:
            return []
        return self.bundle.explain(score.features)[0]
