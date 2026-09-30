"""One encounter's observations, bucketed into ICU hours.

ICU hour k covers (admission + (k - 1) h, admission + k h], the PhysioNet
convention in which ICULOS 1 is the first hour. The simulator stamps hour k's
results at exactly admission + k h, so that time falls in hour k.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

import numpy as np
import pandas as pd
from wardwatch_ml.contracts import loinc_by_code
from wardwatch_ml.features import MAPPED_VARIABLES

HOUR = timedelta(hours=1)


class Added(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    LATE = "late"
    BEFORE_ADMISSION = "before_admission"
    UNKNOWN_CODE = "unknown_code"


def icu_hour(effective: datetime, admitted: datetime) -> int:
    """The ICU hour an instant falls in; 0 or less means at or before admission."""
    return math.ceil((effective - admitted) / HOUR)


@dataclass
class EncounterWindow:
    mrn: str
    encounter_id: str
    admitted_at: datetime
    # hour -> variable -> (effective time, observation ID, value)
    values: dict[int, dict[str, tuple[datetime, str, float]]] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    # MSH-7 of the newest message that contributed to each hour.
    message_times: dict[int, str] = field(default_factory=dict)
    last_scored_hour: int = 0
    last_arrival: float = 0.0

    @property
    def newest_hour(self) -> int:
        return max(self.values, default=0)

    def add(
        self, observation: dict[str, Any], message_time: str, arrival: float
    ) -> tuple[Added, int]:
        """Place one FHIR Observation; returns what happened and its ICU hour."""
        observation_id = str(observation["id"])
        if observation_id in self.seen:
            return Added.DUPLICATE, 0
        entry = loinc_by_code().get(observation["code"]["coding"][0]["code"])
        if entry is None:
            return Added.UNKNOWN_CODE, 0
        effective = datetime.fromisoformat(observation["effectiveDateTime"])
        hour = icu_hour(effective, self.admitted_at)
        if hour < 1:
            return Added.BEFORE_ADMISSION, hour
        self.seen.add(observation_id)
        value = float(observation["valueQuantity"]["value"])
        slot = self.values.setdefault(hour, {})
        current = slot.get(entry.physionet_variable)
        # Two results for one variable in one hour: the later one stands,
        # which matches one PhysioNet row holding one value per hour.
        if current is None or (effective, observation_id) > (current[0], current[1]):
            slot[entry.physionet_variable] = (effective, observation_id, value)
        if message_time and message_time > self.message_times.get(hour, ""):
            self.message_times[hour] = message_time
        self.last_arrival = arrival
        return (Added.LATE if hour <= self.last_scored_hour else Added.ACCEPTED), hour

    def rows(self, up_to_hour: int, history_hours: int) -> pd.DataFrame:
        """PhysioNet-shaped rows for hours up_to_hour - history_hours + 1 .. up_to_hour."""
        first = max(1, up_to_hour - history_hours + 1)
        hours = list(range(first, up_to_hour + 1))
        table: dict[str, Any] = {
            "site": "online",
            "patient_id": self.encounter_id,
            "hour": np.array(hours, dtype=np.int64),
        }
        for variable in MAPPED_VARIABLES:
            table[variable] = np.array(
                [
                    self.values.get(hour, {}).get(variable, (None, None, np.nan))[2]
                    for hour in hours
                ],
                dtype=np.float64,
            )
        return pd.DataFrame(table)

    def hour_ending(self, hour: int) -> datetime:
        return self.admitted_at + hour * HOUR
