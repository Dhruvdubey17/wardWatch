"""Gives each replayed PhysioNet stay a Synthea identity, deterministically.

PhysioNet supplies the physiology and Synthea the person: name, MRN, birth
date and address. A stay gets a free identity of the same gender whose age at
admission is closest to the stay's recorded age, with ties broken by a seeded
hash. No identity is handed to two beds at once.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from wardwatch_sim.hl7 import Gender, PatientIdentity


def age_on(birth_date: date, day: date) -> float:
    """Age in years, counting whole years and the fraction of the current one."""
    years = day.year - birth_date.year - ((day.month, day.day) < (birth_date.month, birth_date.day))
    last_birthday = _replace_year(birth_date, birth_date.year + years)
    next_birthday = _replace_year(birth_date, birth_date.year + years + 1)
    return years + (day - last_birthday).days / (next_birthday - last_birthday).days


def _replace_year(day: date, year: int) -> date:
    # 29 February falls back to 28 February in non-leap years.
    try:
        return day.replace(year=year)
    except ValueError:
        return day.replace(year=year, day=28)


def _tie_break(seed: int, stay_key: str, mrn: str) -> str:
    return hashlib.sha256(f"{seed}:{stay_key}:{mrn}".encode()).hexdigest()


class IdentityPoolExhaustedError(RuntimeError):
    pass


@dataclass
class IdentityAssigner:
    pool: Sequence[PatientIdentity]
    seed: int

    def __post_init__(self) -> None:
        mrns = [identity.mrn for identity in self.pool]
        if len(set(mrns)) != len(mrns):
            raise ValueError("identity pool has duplicate MRNs")
        self._in_use: set[str] = set()

    @property
    def in_use(self) -> frozenset[str]:
        return frozenset(self._in_use)

    def assign(
        self, stay_key: str, gender: Gender | None, age_years: float | None, admitted_on: date
    ) -> PatientIdentity:
        """Pick and reserve an identity for one stay; release() frees it at discharge."""
        free = [identity for identity in self.pool if identity.mrn not in self._in_use]
        if not free:
            raise IdentityPoolExhaustedError(
                f"all {len(self.pool)} identities are admitted; generate more Synthea patients"
            )
        same_gender = [identity for identity in free if identity.gender == gender]
        candidates = same_gender or free

        def rank(identity: PatientIdentity) -> tuple[float, str]:
            distance = (
                0.0
                if age_years is None
                else abs(age_on(identity.birth_date, admitted_on) - age_years)
            )
            return (distance, _tie_break(self.seed, stay_key, identity.mrn))

        chosen = min(candidates, key=rank)
        self._in_use.add(chosen.mrn)
        return chosen

    def release(self, mrn: str) -> None:
        self._in_use.discard(mrn)
