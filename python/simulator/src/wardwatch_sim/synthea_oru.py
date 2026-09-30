"""Turns one Synthea patient's vital signs into ADT and ORU^R01 messages.

This path exists to test identity and value round trips through the pipeline
with data that did not come from PhysioNet. Observations taken at the same
moment become one ORU^R01; codes outside the LOINC table (weight, BMI, pain)
are left out, as are values whose unit differs from the table's UCUM unit.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from wardwatch_ml.contracts import loinc_by_code

from wardwatch_sim.control_ids import ControlIdSequence
from wardwatch_sim.hl7 import (
    Measurement,
    MessageHeader,
    Visit,
    build_adt_a01,
    build_adt_a03,
    build_oru_r01,
)
from wardwatch_sim.synthea import SyntheaPatient, VitalSign


@dataclass(frozen=True)
class ConvertedPatient:
    messages: tuple[str, ...]
    skipped: tuple[VitalSign, ...]


def _usable(sign: VitalSign) -> bool:
    entry = loinc_by_code().get(sign.loinc)
    return entry is not None and entry.ucum_unit == sign.unit


def convert_patient(patient: SyntheaPatient, control_ids: ControlIdSequence) -> ConvertedPatient:
    """Admit at the first vital sign, one ORU per timestamp, discharge at the last."""
    usable = [sign for sign in patient.vital_signs if _usable(sign)]
    skipped = tuple(sign for sign in patient.vital_signs if not _usable(sign))
    if not usable:
        return ConvertedPatient(messages=(), skipped=skipped)

    by_time: dict[datetime, list[VitalSign]] = defaultdict(list)
    for sign in usable:
        by_time[sign.effective_at].append(sign)
    times = sorted(by_time)
    visit = Visit(
        visit_number=f"SYN-{patient.identity.mrn[:8]}",
        room="00",
        bed="SYN",
        admitted_at=times[0],
        discharged_at=times[-1],
    )
    identity = patient.identity
    messages = [build_adt_a01(MessageHeader(control_ids.next(), times[0]), identity, visit)]
    for index, moment in enumerate(times, start=1):
        signs = sorted(by_time[moment], key=lambda sign: sign.loinc)
        messages.append(
            build_oru_r01(
                MessageHeader(control_ids.next(), moment),
                identity,
                visit,
                order_id=f"{visit.visit_number}-S{index:04d}",
                observed_at=moment,
                measurements=[Measurement(sign.loinc, sign.value, moment) for sign in signs],
            )
        )
    messages.append(build_adt_a03(MessageHeader(control_ids.next(), times[-1]), identity, visit))
    return ConvertedPatient(messages=tuple(messages), skipped=skipped)
