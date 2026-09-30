"""Builds the HL7 v2.5.1 messages the simulator sends: ADT^A01, ADT^A03 and ORU^R01."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from wardwatch_ml.contracts import loinc_by_code

FIELD = "|"
COMPONENT = "^"
REPETITION = "~"
ESCAPE = "\\"
SUBCOMPONENT = "&"
ENCODING_CHARACTERS = COMPONENT + REPETITION + ESCAPE + SUBCOMPONENT
SEGMENT_TERMINATOR = "\r"
VERSION = "2.5.1"
ASSIGNING_AUTHORITY = "WARDWATCH"

# HL7 v2.5.1 section 2.7.1: each delimiter inside data is written as an escape.
_ESCAPES = {
    ESCAPE: f"{ESCAPE}E{ESCAPE}",
    FIELD: f"{ESCAPE}F{ESCAPE}",
    COMPONENT: f"{ESCAPE}S{ESCAPE}",
    SUBCOMPONENT: f"{ESCAPE}T{ESCAPE}",
    REPETITION: f"{ESCAPE}R{ESCAPE}",
    "\r": f"{ESCAPE}X0D{ESCAPE}",
    "\n": f"{ESCAPE}X0A{ESCAPE}",
}

Gender = Literal["F", "M", "U"]


def escape(text: str) -> str:
    """Escape delimiters and line breaks so text is safe inside one component."""
    return "".join(_ESCAPES.get(character, character) for character in text)


def composite(*components: str) -> str:
    """Join already-escaped components, dropping empty trailing ones."""
    parts = list(components)
    while parts and not parts[-1]:
        parts.pop()
    return COMPONENT.join(parts)


def format_nm(value: float) -> str:
    """Positional decimal with the fewest digits that read back as the same float.

    NM has no exponent form (HL7 v2.5.1 section 2.A.47), so repr() alone is not
    enough for values such as 1e-05.
    """
    if not math.isfinite(value):
        raise ValueError(f"{value} cannot be written as an NM value")
    text = format(Decimal(repr(value)), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def format_dtm(moment: datetime) -> str:
    """DTM to the second in UTC with an explicit +0000 offset."""
    if moment.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return moment.astimezone(UTC).strftime("%Y%m%d%H%M%S") + "+0000"


def format_date(day: date) -> str:
    return day.strftime("%Y%m%d")


@dataclass(frozen=True)
class PatientIdentity:
    mrn: str
    family: str
    given: str
    birth_date: date
    gender: Gender
    middle: str = ""
    street: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""


@dataclass(frozen=True)
class Visit:
    visit_number: str
    room: str
    bed: str
    admitted_at: datetime
    discharged_at: datetime | None = None


@dataclass(frozen=True)
class MessageHeader:
    control_id: str
    sent_at: datetime
    sending_application: str = "WWSIM"
    sending_facility: str = "WARDWATCH_ICU"
    receiving_application: str = "WARDWATCH"
    receiving_facility: str = "WARDWATCH"


@dataclass(frozen=True)
class Measurement:
    loinc: str
    value: float
    effective_at: datetime


def _segment(segment_id: str, *fields: str) -> str:
    values = list(fields)
    # Trailing empty fields are omitted (HL7 v2.5.1 section 2.5.3); the
    # ingest engine warns about them.
    while values and not values[-1]:
        values.pop()
    return FIELD.join([segment_id, *values])


def _msh(header: MessageHeader, message_type: str, trigger: str, structure: str) -> list[str]:
    # MSH-1 is the field separator itself, so the joined segment starts
    # "MSH|^~\&|"; fields below are MSH-3 onward.
    return [
        escape(header.sending_application),
        escape(header.sending_facility),
        escape(header.receiving_application),
        escape(header.receiving_facility),
        format_dtm(header.sent_at),
        "",
        composite(message_type, trigger, structure),
        escape(header.control_id),
        "P",
        VERSION,
    ]


def _header_segment(fields: list[str], has_non_ascii: bool) -> str:
    if has_non_ascii:
        # MSH-18 declares the character set; without it the default is ASCII.
        fields = fields + [""] * (15 - len(fields)) + ["UNICODE UTF-8"]
    return "MSH" + FIELD + ENCODING_CHARACTERS + FIELD + FIELD.join(_trim(fields))


def _trim(fields: list[str]) -> list[str]:
    values = list(fields)
    while values and not values[-1]:
        values.pop()
    return values


def _pid(patient: PatientIdentity) -> str:
    return _segment(
        "PID",
        "1",
        "",
        composite(escape(patient.mrn), "", "", ASSIGNING_AUTHORITY, "MR"),
        "",
        composite(escape(patient.family), escape(patient.given), escape(patient.middle)),
        "",
        format_date(patient.birth_date),
        patient.gender,
        "",
        "",
        composite(
            escape(patient.street),
            "",
            escape(patient.city),
            escape(patient.state),
            escape(patient.postal_code),
            "USA" if patient.street or patient.city else "",
        ),
    )


def _pv1(visit: Visit, *, admit: bool, discharge: bool) -> str:
    fields = ["" for _ in range(45)]
    fields[0] = "1"
    fields[1] = "I"
    fields[2] = composite("ICU", escape(visit.room), escape(visit.bed), ASSIGNING_AUTHORITY)
    fields[18] = composite(escape(visit.visit_number), "", "", ASSIGNING_AUTHORITY, "VN")
    if admit:
        fields[43] = format_dtm(visit.admitted_at)
    if discharge and visit.discharged_at is not None:
        fields[44] = format_dtm(visit.discharged_at)
    return _segment("PV1", *fields)


def _finish(header_fields: list[str], segments: list[str]) -> str:
    body = SEGMENT_TERMINATOR.join(segments)
    has_non_ascii = any(ord(character) > 0x7F for character in body)
    header = _header_segment(header_fields, has_non_ascii)
    return SEGMENT_TERMINATOR.join([header, *segments]) + SEGMENT_TERMINATOR


def build_adt_a01(header: MessageHeader, patient: PatientIdentity, visit: Visit) -> str:
    """Admit: MSH, EVN, PID and PV1 with the admission time in PV1-44."""
    return _finish(
        _msh(header, "ADT", "A01", "ADT_A01"),
        [
            _segment("EVN", "A01", format_dtm(visit.admitted_at)),
            _pid(patient),
            _pv1(visit, admit=True, discharge=False),
        ],
    )


def build_adt_a03(header: MessageHeader, patient: PatientIdentity, visit: Visit) -> str:
    """Discharge: like the admit, with PV1-45 set to the discharge time."""
    if visit.discharged_at is None:
        raise ValueError("a discharge needs visit.discharged_at")
    return _finish(
        _msh(header, "ADT", "A03", "ADT_A03"),
        [
            _segment("EVN", "A03", format_dtm(visit.discharged_at)),
            _pid(patient),
            _pv1(visit, admit=True, discharge=True),
        ],
    )


def build_oru_r01(
    header: MessageHeader,
    patient: PatientIdentity,
    visit: Visit,
    *,
    order_id: str,
    observed_at: datetime,
    measurements: Sequence[Measurement],
) -> str:
    """One hourly result: OBR-7 is the observation time and each OBX one measurement."""
    if not measurements:
        raise ValueError("an ORU^R01 needs at least one measurement")
    codes = loinc_by_code()
    segments = [
        _pid_short(patient),
        _pv1(visit, admit=False, discharge=False),
        _segment(
            "OBR",
            "1",
            "",
            composite(escape(order_id), header.sending_application),
            composite("WW-HOURLY", "Hourly ICU observations", "L"),
            "",
            "",
            format_dtm(observed_at),
        ),
    ]
    for set_id, measurement in enumerate(measurements, start=1):
        code = codes[measurement.loinc]
        unit = escape(code.ucum_unit)
        segments.append(
            _segment(
                "OBX",
                str(set_id),
                "NM",
                composite(code.code, escape(code.display), "LN"),
                "",
                format_nm(measurement.value),
                composite(unit, unit, "UCUM"),
                "",
                "",
                "",
                "",
                "F",
                "",
                "",
                format_dtm(measurement.effective_at),
            )
        )
    return _finish(_msh(header, "ORU", "R01", "ORU_R01"), segments)


def _pid_short(patient: PatientIdentity) -> str:
    # Result messages carry only what links them to the patient; the address
    # travels with the ADT.
    return _segment(
        "PID",
        "1",
        "",
        composite(escape(patient.mrn), "", "", ASSIGNING_AUTHORITY, "MR"),
        "",
        composite(escape(patient.family), escape(patient.given), escape(patient.middle)),
        "",
        format_date(patient.birth_date),
        patient.gender,
    )
