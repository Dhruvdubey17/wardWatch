"""Locates the repository's contracts/ directory and loads the LOINC table."""

import json
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

CONTRACTS_ENV = "WARDWATCH_CONTRACTS_DIR"


def contracts_dir() -> Path:
    """Return contracts/, from WARDWATCH_CONTRACTS_DIR or by searching upward."""
    configured = os.environ.get(CONTRACTS_ENV)
    if configured:
        return Path(configured)
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "contracts"
        if (candidate / "loinc_codes.json").is_file():
            return candidate
    raise FileNotFoundError(
        f"contracts/ not found above {__file__}; set {CONTRACTS_ENV} to its location"
    )


@dataclass(frozen=True)
class LoincCode:
    code: str
    display: str
    physionet_variable: str
    ucum_unit: str
    category: Literal["vital-signs", "laboratory"]
    plausible_min: float
    plausible_max: float


@cache
def loinc_table() -> tuple[LoincCode, ...]:
    """The LOINC codes carried in OBX-3, in contract order."""
    document = json.loads((contracts_dir() / "loinc_codes.json").read_text())
    return tuple(LoincCode(**entry) for entry in document["codes"])


def loinc_by_code() -> dict[str, LoincCode]:
    return {entry.code: entry for entry in loinc_table()}


def loinc_by_variable() -> dict[str, LoincCode]:
    return {entry.physionet_variable: entry for entry in loinc_table()}
