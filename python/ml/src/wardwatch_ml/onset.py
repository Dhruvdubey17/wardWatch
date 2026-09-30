"""Recovers each stay's sepsis onset from the shifted PhysioNet label.

The 2019 Challenge sets SepsisLabel to 1 from six hours before onset
(t_sepsis - 6) onward, so the true onset is the first labelled hour plus six.
A stay labelled from its first row has an unknown onset: the shift may have
been cut off by the start of the record.
"""

from dataclasses import dataclass

import pandas as pd

from wardwatch_ml.data import LABEL

LABEL_LEAD_HOURS = 6


@dataclass(frozen=True)
class StayOutcome:
    site: str
    patient_id: str
    septic: bool
    onset_known: bool
    # ICU hour (ICULOS) of onset; None for non-septic stays and unknown onsets.
    onset_hour: int | None
    first_hour: int
    last_hour: int


def stay_outcome(stay: pd.DataFrame) -> StayOutcome:
    """Outcome for one stay's rows, which must be ordered by hour."""
    hours = stay["hour"].to_numpy()
    labels = stay[LABEL].to_numpy()
    site = str(stay["site"].iloc[0])
    patient_id = str(stay["patient_id"].iloc[0])
    first_hour, last_hour = int(hours[0]), int(hours[-1])
    positive = labels == 1
    if not positive.any():
        return StayOutcome(site, patient_id, False, False, None, first_hour, last_hour)
    first_positive = int(positive.argmax())
    if first_positive == 0:
        return StayOutcome(site, patient_id, True, False, None, first_hour, last_hour)
    onset = int(hours[first_positive]) + LABEL_LEAD_HOURS
    return StayOutcome(site, patient_id, True, True, onset, first_hour, last_hour)


def stay_outcomes(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per stay with septic, onset_known, onset_hour, first_hour and last_hour."""
    outcomes = [stay_outcome(stay) for _, stay in frame.groupby(["site", "patient_id"], sort=True)]
    table = pd.DataFrame([outcome.__dict__ for outcome in outcomes])
    table["onset_hour"] = table["onset_hour"].astype("Int64")
    return table
