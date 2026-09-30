"""Reads PhysioNet 2019 Challenge .psv files into long-format tables.

One file holds one ICU stay, one row per ICU hour. The long table keeps that
shape and adds `site` and `patient_id`, so rows from many stays and both
hospital systems can sit in one frame.
"""

from collections.abc import Iterator
from pathlib import Path

import pandas as pd

VITALS = ("HR", "O2Sat", "Temp", "SBP", "MAP", "DBP", "Resp", "EtCO2")
LABS = (
    "BaseExcess",
    "HCO3",
    "FiO2",
    "pH",
    "PaCO2",
    "SaO2",
    "AST",
    "BUN",
    "Alkalinephos",
    "Calcium",
    "Chloride",
    "Creatinine",
    "Bilirubin_direct",
    "Glucose",
    "Lactate",
    "Magnesium",
    "Phosphate",
    "Potassium",
    "Bilirubin_total",
    "TroponinI",
    "Hct",
    "Hgb",
    "PTT",
    "WBC",
    "Fibrinogen",
    "Platelets",
)
DEMOGRAPHICS = ("Age", "Gender", "Unit1", "Unit2", "HospAdmTime", "ICULOS")
LABEL = "SepsisLabel"
PSV_COLUMNS = (*VITALS, *LABS, *DEMOGRAPHICS, LABEL)

# The two training sets come from two different hospital systems; the
# evaluation trains on one and tests on the other.
SITE_DIRECTORIES = {"A": "training_setA", "B": "training_setB"}

KEY_COLUMNS = ("site", "patient_id", "hour")


def read_stay(path: Path, site: str) -> pd.DataFrame:
    """Read one .psv file, adding site, patient_id and an integer hour (ICULOS)."""
    frame = pd.read_csv(path, sep="|", na_values=["NaN"], keep_default_na=False)
    if tuple(frame.columns) != PSV_COLUMNS:
        raise ValueError(f"{path} does not have the 41 PhysioNet 2019 columns")
    frame = frame.astype("float64")
    frame.insert(0, "hour", frame["ICULOS"].astype("int64"))
    frame.insert(0, "patient_id", path.stem)
    frame.insert(0, "site", site)
    frame[LABEL] = frame[LABEL].astype("int64")
    return frame


def stay_paths(directory: Path) -> list[Path]:
    """The .psv files in a site directory, sorted by patient ID."""
    return sorted(directory.glob("*.psv"))


def iter_stays(directory: Path, site: str, limit: int | None = None) -> Iterator[pd.DataFrame]:
    for path in stay_paths(directory)[:limit]:
        yield read_stay(path, site)


def load_site(directory: Path, site: str, limit: int | None = None) -> pd.DataFrame:
    """Every stay in one site directory as one long table ordered by patient and hour."""
    stays = list(iter_stays(directory, site, limit))
    if not stays:
        raise FileNotFoundError(f"no .psv files in {directory}")
    frame = pd.concat(stays, ignore_index=True)
    return frame.sort_values(["patient_id", "hour"], kind="stable").reset_index(drop=True)


def load_physionet(root: Path, sites: tuple[str, ...] = ("A", "B")) -> pd.DataFrame:
    """Load several sites from a directory holding training_setA and training_setB."""
    return pd.concat(
        [load_site(root / SITE_DIRECTORIES[site], site) for site in sites], ignore_index=True
    )
