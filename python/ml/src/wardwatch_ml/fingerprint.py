"""A fingerprint of exactly which rows reached a fitted artifact.

Stored in every serving bundle and evaluation report, so a test can show that
no test-site stay was used to fit a model, calibrator or threshold.
"""

import hashlib
from dataclasses import asdict, dataclass

import pandas as pd

from wardwatch_ml.data import PSV_COLUMNS


@dataclass(frozen=True)
class DataFingerprint:
    sites: tuple[str, ...]
    stays: int
    rows: int
    # SHA-256 over the sorted stay keys and a hash of every value in those rows.
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def data_fingerprint(frame: pd.DataFrame) -> DataFingerprint:
    ordered = frame.sort_values(["site", "patient_id", "hour"], kind="stable")
    digest = hashlib.sha256()
    for site, patient in ordered[["site", "patient_id"]].drop_duplicates().itertuples(index=False):
        digest.update(f"{site}/{patient}\n".encode())
    columns = [column for column in PSV_COLUMNS if column in ordered.columns]
    row_hashes = pd.util.hash_pandas_object(ordered[columns], index=False).to_numpy()
    digest.update(row_hashes.tobytes())
    return DataFingerprint(
        sites=tuple(sorted(str(site) for site in ordered["site"].unique())),
        stays=int(ordered[["site", "patient_id"]].drop_duplicates().shape[0]),
        rows=len(ordered),
        sha256=digest.hexdigest(),
    )
