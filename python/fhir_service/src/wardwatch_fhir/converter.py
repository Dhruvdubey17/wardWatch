"""Converts hl7.validated payloads into FHIR R4 Patient, Encounter and Observation.

Pure functions over the payload's decoded segment arrays; nothing here touches
the database. The full mapping is in docs/hl7-fhir-mapping.md.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from wardwatch_ml.contracts import loinc_by_code

MRN_SYSTEM = "https://wardwatch.local/fhir/sid/mrn"
VISIT_SYSTEM = "https://wardwatch.local/fhir/sid/visit-number"
LOINC_SYSTEM = "http://loinc.org"
UCUM_SYSTEM = "http://unitsofmeasure.org"
CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/observation-category"
IDENTIFIER_TYPE_SYSTEM = "http://terminology.hl7.org/CodeSystem/v2-0203"
ACT_CODE_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ActCode"

CATEGORY_DISPLAY = {"vital-signs": "Vital Signs", "laboratory": "Laboratory"}

# HL7 v2 table 0001 (administrative sex) to FHIR AdministrativeGender.
GENDERS = {"F": "female", "M": "male", "O": "other", "U": "unknown", "A": "other", "N": "unknown"}

# HL7 v2 table 0085 (observation result status) to FHIR ObservationStatus,
# following the HL7 v2-to-FHIR ConceptMap for OBX-11.
RESULT_STATUSES = {
    "F": "final",
    "C": "corrected",
    "A": "amended",
    "U": "final",
    "P": "preliminary",
    "R": "preliminary",
    "S": "preliminary",
    "I": "registered",
    "O": "registered",
    "X": "cancelled",
    "N": "cancelled",
    "D": "entered-in-error",
    "W": "entered-in-error",
}

_DTM = re.compile(
    r"^(?P<year>\d{4})(?P<month>\d{2})?(?P<day>\d{2})?(?P<hour>\d{2})?(?P<minute>\d{2})?"
    r"(?P<second>\d{2})?(?:\.(?P<fraction>\d{1,4}))?(?P<offset>[+-]\d{4})?$"
)
_FHIR_ID = re.compile(r"[^A-Za-z0-9.\-]")


class ConversionError(ValueError):
    """A validated message that still cannot become FHIR; dead-lettered with stage=fhir."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Converted:
    mrn: str
    encounter_id: str
    message_type: str
    trigger_event: str
    message_time: str
    patient: dict[str, Any]
    encounter: dict[str, Any]
    observations: list[dict[str, Any]] = field(default_factory=list)
    skipped_observations: int = 0


class Segments:
    """Field access into the payload's segments, numbered like the HL7 standard."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._segments: list[dict[str, Any]] = payload["segments"]

    def all(self, segment_id: str) -> list[list[Any]]:
        return [segment["fields"] for segment in self._segments if segment["id"] == segment_id]

    def first(self, segment_id: str) -> list[Any] | None:
        found = self.all(segment_id)
        return found[0] if found else None

    @staticmethod
    def value(fields: list[Any], number: int, component: int = 1, repetition: int = 1) -> str:
        """Field `number`, repetition, component (all 1-based), first subcomponent; "" if absent."""
        if number > len(fields):
            return ""
        repetitions = fields[number - 1]
        if repetition > len(repetitions):
            return ""
        components = repetitions[repetition - 1]
        if component > len(components):
            return ""
        return str(components[component - 1][0])


def fhir_id(text: str) -> str:
    """A FHIR id (1 to 64 of A-Z, a-z, 0-9, '-', '.') derived from an identifier."""
    cleaned = _FHIR_ID.sub("-", text)[:64]
    if not cleaned:
        raise ConversionError("FHIR_ID_EMPTY", f"'{text}' gives an empty FHIR id")
    return cleaned


def fhir_datetime(dtm: str, field_name: str) -> str:
    """HL7 DTM to FHIR dateTime.

    FHIR requires seconds and a timezone once a time is present, so a time to
    the hour or minute is padded with zeros and a missing offset is read as UTC.
    """
    match = _DTM.match(dtm)
    if not match:
        raise ConversionError("TIMESTAMP_INVALID", f"{field_name} '{dtm}' is not a DTM")
    parts = match.groupdict()
    if parts["hour"] is None:
        text = parts["year"]
        if parts["month"]:
            text += f"-{parts['month']}"
        if parts["day"]:
            text += f"-{parts['day']}"
        return text
    offset = parts["offset"] or "+0000"
    zone = timezone(
        (1 if offset[0] == "+" else -1) * timedelta(hours=int(offset[1:3]), minutes=int(offset[3:]))
    )
    moment = datetime(
        int(parts["year"]),
        int(parts["month"]),
        int(parts["day"]),
        int(parts["hour"]),
        int(parts["minute"] or 0),
        int(parts["second"] or 0),
        int((parts["fraction"] or "0").ljust(6, "0")),
        tzinfo=zone,
    )
    return moment.isoformat()


def fhir_date(dtm: str, field_name: str) -> str:
    """HL7 DTM to FHIR date: only the date part, at its own precision."""
    return fhir_datetime(dtm[:8], field_name)


def parse_fhir_datetime(text: str) -> datetime:
    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _patient(segments: Segments, mrn: str) -> dict[str, Any]:
    pid = segments.first("PID")
    if pid is None:
        raise ConversionError("PID_MISSING", "message has no PID segment")
    value = Segments.value
    given = [part for part in (value(pid, 5, 2), value(pid, 5, 3)) if part]
    name: dict[str, Any] = {"use": "official", "family": value(pid, 5, 1)}
    if given:
        name["given"] = given
    resource: dict[str, Any] = {
        "resourceType": "Patient",
        "id": fhir_id(mrn),
        "identifier": [
            {
                "use": "usual",
                "type": {"coding": [{"system": IDENTIFIER_TYPE_SYSTEM, "code": "MR"}]},
                "system": MRN_SYSTEM,
                "value": mrn,
            }
        ],
        "name": [name],
        "gender": GENDERS.get(value(pid, 8), "unknown"),
    }
    birth = value(pid, 7)
    if birth:
        resource["birthDate"] = fhir_date(birth, "PID-7")
    street, city, state, postal = (value(pid, 11, component) for component in (1, 3, 4, 5))
    if street or city:
        address: dict[str, Any] = {"use": "home"}
        if street:
            address["line"] = [street]
        if city:
            address["city"] = city
        if state:
            address["state"] = state
        if postal:
            address["postalCode"] = postal
        country = value(pid, 11, 6)
        if country:
            address["country"] = country
        resource["address"] = [address]
    return resource


def _encounter(segments: Segments, patient_id: str, trigger: str) -> dict[str, Any]:
    pv1 = segments.first("PV1")
    if pv1 is None:
        raise ConversionError("PV1_MISSING", "message has no PV1 segment")
    value = Segments.value
    visit = value(pv1, 19)
    if not visit:
        raise ConversionError("VISIT_NUMBER_MISSING", "PV1-19 has no visit number")
    location = " ".join(
        part for part in (value(pv1, 3, 1), value(pv1, 3, 2), value(pv1, 3, 3)) if part
    )
    resource: dict[str, Any] = {
        "resourceType": "Encounter",
        "id": fhir_id(visit),
        "identifier": [{"system": VISIT_SYSTEM, "value": visit}],
        # A03 is a discharge; admissions and results describe an ongoing stay.
        "status": "finished" if trigger == "A03" else "in-progress",
        "class": {"system": ACT_CODE_SYSTEM, "code": "IMP", "display": "inpatient encounter"},
        "subject": {"reference": f"Patient/{patient_id}"},
    }
    period: dict[str, str] = {}
    if value(pv1, 44):
        period["start"] = fhir_datetime(value(pv1, 44), "PV1-44")
    if value(pv1, 45):
        period["end"] = fhir_datetime(value(pv1, 45), "PV1-45")
    if period:
        resource["period"] = period
    if location:
        resource["location"] = [{"location": {"display": location}}]
    return resource


def _observations(
    segments: Segments, control_id: str, patient_id: str, encounter_id: str
) -> tuple[list[dict[str, Any]], int]:
    value = Segments.value
    obr = segments.first("OBR")
    fallback_time = value(obr, 7) if obr else ""
    codes = loinc_by_code()
    observations = []
    skipped = 0
    for obx in segments.all("OBX"):
        code = value(obx, 3, 1)
        entry = codes.get(code) if value(obx, 3, 3) == "LN" else None
        raw_value = value(obx, 5)
        if entry is None or value(obx, 2) != "NM" or not raw_value:
            # Only numeric results for codes in the LOINC table feed the
            # scorer; anything else was accepted by ingest but is not
            # converted, and is counted instead.
            skipped += 1
            continue
        status = RESULT_STATUSES.get(value(obx, 11))
        if status is None:
            raise ConversionError(
                "RESULT_STATUS_UNMAPPED", f"OBX-11 '{value(obx, 11)}' has no FHIR status"
            )
        # OBX-14 is often empty in feeds from bedside monitors, so fall back to OBR-7.
        effective = value(obx, 14) or fallback_time
        if not effective:
            raise ConversionError("OBSERVATION_TIME_MISSING", "neither OBX-14 nor OBR-7 is present")
        unit = value(obx, 6, 1) or entry.ucum_unit
        observations.append(
            {
                "resourceType": "Observation",
                "id": fhir_id(f"{control_id}-{value(obx, 1)}"),
                "status": status,
                "category": [
                    {
                        "coding": [
                            {
                                "system": CATEGORY_SYSTEM,
                                "code": entry.category,
                                "display": CATEGORY_DISPLAY[entry.category],
                            }
                        ]
                    }
                ],
                "code": {
                    "coding": [
                        {"system": LOINC_SYSTEM, "code": entry.code, "display": entry.display}
                    ],
                    "text": value(obx, 3, 2) or entry.display,
                },
                "subject": {"reference": f"Patient/{patient_id}"},
                "encounter": {"reference": f"Encounter/{encounter_id}"},
                "effectiveDateTime": fhir_datetime(effective, "OBX-14"),
                "valueQuantity": {
                    "value": float(raw_value),
                    "unit": unit,
                    "system": UCUM_SYSTEM,
                    "code": unit,
                },
            }
        )
    return observations, skipped


def convert(payload: dict[str, Any]) -> Converted:
    """Convert one hl7.validated payload; raises ConversionError when it cannot."""
    segments = Segments(payload)
    mrn = str(payload["mrn"])
    trigger = str(payload["trigger_event"])
    patient = _patient(segments, mrn)
    encounter = _encounter(segments, patient["id"], trigger)
    observations: list[dict[str, Any]] = []
    skipped = 0
    if payload["message_type"] == "ORU":
        observations, skipped = _observations(
            segments, str(payload["control_id"]), patient["id"], encounter["id"]
        )
    return Converted(
        mrn=mrn,
        encounter_id=encounter["id"],
        message_type=str(payload["message_type"]),
        trigger_event=trigger,
        message_time=str(payload["message_time"]),
        patient=patient,
        encounter=encounter,
        observations=observations,
        skipped_observations=skipped,
    )
