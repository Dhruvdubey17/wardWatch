"""Reads Synthea FHIR R4 bundles: the patient identity and its vital-sign observations."""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from wardwatch_sim.hl7 import Gender, PatientIdentity

_MR_TYPE = "MR"
_GENDERS: dict[str, Gender] = {"female": "F", "male": "M"}
# Synthea appends digits to generated names ("Lindgren255") so they are
# unique; they are dropped so the names read like names.
_NAME_SUFFIX = re.compile(r"\d+$")


@dataclass(frozen=True)
class VitalSign:
    loinc: str
    value: float
    unit: str
    effective_at: datetime


@dataclass(frozen=True)
class SyntheaPatient:
    identity: PatientIdentity
    vital_signs: tuple[VitalSign, ...]


def _clean_name(name: str) -> str:
    return _NAME_SUFFIX.sub("", name)


def _medical_record_number(patient: dict[str, Any]) -> str:
    for identifier in patient.get("identifier", []):
        codings = identifier.get("type", {}).get("coding", [])
        if any(coding.get("code") == _MR_TYPE for coding in codings):
            return str(identifier["value"])
    raise ValueError(f"Synthea patient {patient.get('id')} has no MR identifier")


def _identity(patient: dict[str, Any]) -> PatientIdentity:
    official = next(
        (name for name in patient.get("name", []) if name.get("use") == "official"),
        patient["name"][0],
    )
    given_names = [_clean_name(name) for name in official.get("given", [])]
    address = (patient.get("address") or [{}])[0]
    return PatientIdentity(
        mrn=_medical_record_number(patient),
        family=_clean_name(official.get("family", "")),
        given=given_names[0] if given_names else "",
        middle=" ".join(given_names[1:]),
        birth_date=date.fromisoformat(patient["birthDate"]),
        gender=_GENDERS.get(patient.get("gender", ""), "U"),
        street=" ".join(address.get("line", [])),
        city=address.get("city", ""),
        state=address.get("state", ""),
        postal_code=address.get("postalCode", ""),
    )


def _quantity_signs(code: str, quantity: dict[str, Any], effective_at: datetime) -> VitalSign:
    return VitalSign(
        loinc=code,
        value=float(quantity["value"]),
        unit=str(quantity.get("code") or quantity.get("unit", "")),
        effective_at=effective_at,
    )


def _vital_signs(resource: dict[str, Any]) -> list[VitalSign]:
    categories = {
        coding.get("code")
        for category in resource.get("category", [])
        for coding in category.get("coding", [])
    }
    if "vital-signs" not in categories or "effectiveDateTime" not in resource:
        return []
    effective_at = datetime.fromisoformat(resource["effectiveDateTime"])
    signs = []
    code = resource["code"]["coding"][0]["code"]
    if "valueQuantity" in resource:
        signs.append(_quantity_signs(code, resource["valueQuantity"], effective_at))
    # Blood pressure arrives as a panel (85354-9) with systolic and diastolic
    # components rather than as two observations.
    for component in resource.get("component", []):
        if "valueQuantity" in component:
            component_code = component["code"]["coding"][0]["code"]
            signs.append(_quantity_signs(component_code, component["valueQuantity"], effective_at))
    return signs


def read_bundle(path: Path) -> SyntheaPatient:
    """Read one Synthea bundle; it must contain exactly one Patient."""
    bundle = json.loads(path.read_text(encoding="utf-8"))
    resources = [entry["resource"] for entry in bundle.get("entry", [])]
    patients = [resource for resource in resources if resource["resourceType"] == "Patient"]
    if len(patients) != 1:
        raise ValueError(f"{path} has {len(patients)} Patient resources, expected 1")
    signs = [
        sign
        for resource in resources
        if resource["resourceType"] == "Observation"
        for sign in _vital_signs(resource)
    ]
    signs.sort(key=lambda sign: (sign.effective_at, sign.loinc))
    return SyntheaPatient(identity=_identity(patients[0]), vital_signs=tuple(signs))


def bundle_paths(directory: Path) -> list[Path]:
    """Patient bundles only; Synthea also writes hospital and practitioner bundles."""
    return sorted(
        path
        for path in directory.glob("*.json")
        if not path.name.startswith(("hospitalInformation", "practitionerInformation"))
    )


def load_patients(directory: Path) -> list[SyntheaPatient]:
    return [read_bundle(path) for path in bundle_paths(directory)]
