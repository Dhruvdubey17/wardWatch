"""Shared helpers for scorer tests: a fixture-trained bundle and stay replay."""

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from wardwatch_ml.bundle import write_bundle
from wardwatch_ml.contracts import loinc_by_variable
from wardwatch_ml.data import load_site, read_stay
from wardwatch_ml.gru_model import GruParams
from wardwatch_ml.training import TrainingConfig, fit_site
from wardwatch_ml.xgb_model import XgbParams

ML_FIXTURES = Path(__file__).resolve().parents[2] / "ml" / "tests" / "fixtures" / "physionet"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
ADMITTED = datetime(2024, 3, 15, 8, 0, tzinfo=UTC)


def train_bundle(directory: Path) -> Path:
    """Train a small bundle under directory and return the bundle's own directory."""
    config = TrainingConfig(
        folds=2,
        calibration_fraction=0.34,
        xgb=XgbParams(max_rounds=30, early_stopping_rounds=10, threads=1),
        gru=GruParams(),
        train_gru=False,
    )
    fitted = fit_site(load_site(ML_FIXTURES / "training_setA", "A"), config)
    return write_bundle(fitted, directory, "xgb-test-0000000", "0" * 40)


def stay_frame(name: str) -> pd.DataFrame:
    return read_stay(FIXTURES / name, "A")


def observation_records(
    frame: pd.DataFrame, encounter_id: str, admitted: datetime = ADMITTED
) -> list[dict[str, Any]]:
    """fhir.observations records for every mapped, non-missing value, in hour order."""
    codes = loinc_by_variable()
    records = []
    for row in frame.to_dict("records"):
        hour = int(row["hour"])
        effective = (admitted + timedelta(hours=hour)).isoformat()
        for variable, entry in codes.items():
            value = row[variable]
            if isinstance(value, float) and math.isnan(value):
                continue
            records.append(
                {
                    "schema_version": 1,
                    "mrn": f"MRN-{encounter_id}",
                    "encounter_id": encounter_id,
                    "encounter_start": admitted.isoformat(),
                    "message_time": (
                        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=hour)
                    ).isoformat(),
                    "observation": {
                        "resourceType": "Observation",
                        "id": f"{encounter_id}-{hour}-{entry.code}",
                        "status": "final",
                        "category": [{"coding": [{"code": entry.category}]}],
                        "subject": {"reference": f"Patient/MRN-{encounter_id}"},
                        "encounter": {"reference": f"Encounter/{encounter_id}"},
                        "code": {"coding": [{"system": "http://loinc.org", "code": entry.code}]},
                        "effectiveDateTime": effective,
                        "valueQuantity": {"value": float(value), "unit": entry.ucum_unit},
                    },
                }
            )
    return records
