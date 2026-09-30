"""The serving bundle: everything the online scorer needs, fitted on one site.

Layout of ml/artifacts/<model_version>/:
  model.ubj    the XGBoost booster in its native binary JSON format
  bundle.json  feature spec, calibrators, frozen operating points, the
               training-data fingerprint, the git commit and versions
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb

from wardwatch_ml.calibration import Calibrator, calibrator_from_dict
from wardwatch_ml.explain import Explainer, Factor
from wardwatch_ml.features import FeatureSpec
from wardwatch_ml.fingerprint import DataFingerprint
from wardwatch_ml.policy import AlertPolicy
from wardwatch_ml.thresholds import OperatingPoint, news2_policy
from wardwatch_ml.training import FittedSite
from wardwatch_ml.xgb_model import predict_margin

MODEL_FILE = "model.ubj"
MANIFEST_FILE = "bundle.json"
SERVED_MODEL = "xgboost"
SERVED_CALIBRATOR = "platt"
SERVED_OPERATING_POINT = "matched_burden"
BUNDLE_FORMAT = 1


def _policy(raw: dict[str, Any]) -> AlertPolicy:
    return AlertPolicy(
        threshold=float(raw["threshold"]),
        refractory_hours=float(raw["refractory_hours"]),
        rise_margin=float(raw["rise_margin"]),
    )


@dataclass
class ServingBundle:
    model_version: str
    booster: xgb.Booster
    spec: FeatureSpec
    calibrator: Calibrator
    policy: AlertPolicy
    news2_policy: AlertPolicy
    manifest: dict[str, Any]
    _explainer: Explainer | None = field(default=None, repr=False)

    @property
    def feature_names(self) -> list[str]:
        return self.spec.feature_names()

    def margin(self, features: pd.DataFrame) -> np.ndarray:
        return predict_margin(self.booster, features, self.feature_names)

    def probability(self, features: pd.DataFrame) -> np.ndarray:
        return self.calibrator.probability(self.margin(features))

    def explain(self, features: pd.DataFrame) -> list[list[Factor]]:
        if self._explainer is None:
            self._explainer = Explainer(self.booster, self.feature_names)
        return self._explainer.top_factors(features)

    @classmethod
    def load(cls, directory: Path) -> "ServingBundle":
        manifest = json.loads((directory / MANIFEST_FILE).read_text())
        if manifest["format"] != BUNDLE_FORMAT:
            raise ValueError(
                f"{directory} is bundle format {manifest['format']}, expected {BUNDLE_FORMAT}"
            )
        booster = xgb.Booster()
        booster.load_model(str(directory / MODEL_FILE))
        spec = FeatureSpec.from_json(json.dumps(manifest["feature_spec"]))
        served = manifest["served"]
        points = manifest["operating_points"]
        return cls(
            model_version=manifest["model_version"],
            booster=booster,
            spec=spec,
            calibrator=calibrator_from_dict(manifest["calibrators"][served["calibrator"]]),
            policy=_policy(points[served["operating_point"]]["policy"]),
            news2_policy=_policy(manifest["news2_policy"]),
            manifest=manifest,
        )


def model_version(git_sha: str, now: datetime | None = None) -> str:
    moment = now or datetime.now(UTC)
    return f"xgb-{moment:%Y%m%d%H%M%S}-{git_sha[:7]}"


def write_bundle(fitted: FittedSite, directory: Path, version: str, git_sha: str) -> Path:
    """Write the XGBoost model and its manifest; returns the bundle directory."""
    model = fitted.models[SERVED_MODEL]
    target = directory / version
    target.mkdir(parents=True, exist_ok=False)
    fitted.xgb.booster.save_model(str(target / MODEL_FILE))
    fingerprint: DataFingerprint = fitted.fingerprints["xgboost"]
    points: dict[str, OperatingPoint] = model.operating_points
    manifest = {
        "format": BUNDLE_FORMAT,
        "model_version": version,
        "created_at": datetime.now(UTC).isoformat(),
        "git_sha": git_sha,
        "train_site": fitted.site,
        "xgboost_version": xgb.__version__,
        "feature_spec": json.loads(fitted.spec.to_json()),
        "feature_names": fitted.spec.feature_names(),
        "calibrators": {
            name: calibrator.to_dict() for name, calibrator in model.calibrators.items()
        },
        "operating_points": {name: point.to_dict() for name, point in points.items()},
        "news2_policy": news2_policy().__dict__,
        "news2_target_alerts_per_day": fitted.news2_target_alerts_per_day,
        "served": {
            "model": SERVED_MODEL,
            "calibrator": SERVED_CALIBRATOR,
            "operating_point": SERVED_OPERATING_POINT,
        },
        "training": fitted.xgb.summary(),
        "data_fingerprint": fingerprint.to_dict(),
        "fingerprints": {name: value.to_dict() for name, value in fitted.fingerprints.items()},
    }
    (target / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return target
