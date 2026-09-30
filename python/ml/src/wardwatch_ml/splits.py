"""Patient-level splits. A stay's hours always land on one side of a split."""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.model_selection import StratifiedKFold, train_test_split

StayKey = tuple[str, str]


def _stay_table(outcomes: pd.DataFrame) -> tuple[list[StayKey], np.ndarray]:
    keys = [
        (str(site), str(patient))
        for site, patient in zip(outcomes["site"], outcomes["patient_id"], strict=True)
    ]
    return keys, outcomes["septic"].to_numpy().astype(int)


@dataclass(frozen=True)
class Fold:
    train: frozenset[StayKey]
    valid: frozenset[StayKey]


def patient_folds(outcomes: pd.DataFrame, folds: int, seed: int) -> list[Fold]:
    """Stratified by whether the stay is septic, so every fold sees both classes."""
    keys, septic = _stay_table(outcomes)
    splitter = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    result = []
    for train_index, valid_index in splitter.split(np.zeros(len(keys)), septic):
        result.append(
            Fold(
                train=frozenset(keys[i] for i in train_index),
                valid=frozenset(keys[i] for i in valid_index),
            )
        )
    return result


def calibration_split(
    outcomes: pd.DataFrame, fraction: float, seed: int
) -> tuple[frozenset[StayKey], frozenset[StayKey]]:
    """Hold out a stratified fraction of stays for fitting calibrators."""
    keys, septic = _stay_table(outcomes)
    fit_index, held_out_index = train_test_split(
        np.arange(len(keys)), test_size=fraction, random_state=seed, stratify=septic, shuffle=True
    )
    return frozenset(keys[i] for i in fit_index), frozenset(keys[i] for i in held_out_index)


def rows_for(frame: pd.DataFrame, stays: frozenset[StayKey]) -> pd.Series:
    """Boolean row mask for the stays in a set."""
    keys = pd.Series(
        list(zip(frame["site"].astype(str), frame["patient_id"].astype(str), strict=True)),
        index=frame.index,
    )
    return keys.isin(stays)
