import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fhir.resources.R4B.encounter import Encounter
from fhir.resources.R4B.observation import Observation
from fhir.resources.R4B.patient import Patient
from jsonschema import Draft202012Validator
from wardwatch_fhir.converter import (
    ConversionError,
    Converted,
    convert,
    fhir_date,
    fhir_datetime,
    fhir_id,
)
from wardwatch_ml.contracts import contracts_dir

pytestmark = pytest.mark.unit

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "golden"
EXAMPLES = contracts_dir() / "examples" / "hl7.validated"


def example(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((EXAMPLES / f"{name}.json").read_text())
    return loaded


def validate_fhir(converted: Converted) -> None:
    Patient.model_validate(converted.patient)
    Encounter.model_validate(converted.encounter)
    for observation in converted.observations:
        Observation.model_validate(observation)


def obx_fields(payload: dict[str, Any]) -> list[list[Any]]:
    return [segment["fields"] for segment in payload["segments"] if segment["id"] == "OBX"]


def set_field(fields: list[Any], number: int, value: str) -> None:
    while len(fields) < number:
        fields.append([])
    fields[number - 1] = [[[value]]] if value else []


@pytest.mark.parametrize("name", ["oru_r01", "adt_a01", "adt_a03_with_warning"])
def test_matches_reviewed_golden_output(name: str) -> None:
    converted = convert(example(name))
    golden = json.loads((GOLDEN / f"{name}.fhir.json").read_text())
    assert converted.patient == golden["patient"]
    assert converted.encounter == golden["encounter"]
    assert converted.observations == golden["observations"]
    assert converted.skipped_observations == golden["skipped_observations"]
    validate_fhir(converted)


def test_oru_gives_one_observation_per_numeric_known_code() -> None:
    converted = convert(example("oru_r01"))
    assert len(converted.observations) == 13
    assert {o["id"] for o in converted.observations} == {f"SIM000007-{i}" for i in range(1, 14)}
    fio2 = next(o for o in converted.observations if o["code"]["coding"][0]["code"] == "3150-0")
    assert fio2["valueQuantity"] == {
        "value": 0.4,
        "unit": "1",
        "system": "http://unitsofmeasure.org",
        "code": "1",
    }
    lactate = next(o for o in converted.observations if o["code"]["coding"][0]["code"] == "2524-7")
    assert lactate["category"][0]["coding"][0]["code"] == "laboratory"


def test_observation_matches_the_fhir_observations_contract() -> None:
    schema = json.loads((contracts_dir() / "schemas" / "fhir.observations.schema.json").read_text())
    validator = Draft202012Validator(schema["properties"]["observation"])
    for observation in convert(example("oru_r01")).observations:
        assert list(validator.iter_errors(observation)) == []


def test_obr7_is_the_fallback_for_an_empty_obx14() -> None:
    payload = example("oru_r01")
    obr = next(s["fields"] for s in payload["segments"] if s["id"] == "OBR")
    set_field(obr, 7, "20240315140000+0000")
    set_field(obx_fields(payload)[0], 14, "")
    first = convert(payload).observations[0]
    assert first["effectiveDateTime"] == "2024-03-15T14:00:00+00:00"


def test_missing_obx14_and_obr7_is_an_error() -> None:
    payload = example("oru_r01")
    obr = next(s["fields"] for s in payload["segments"] if s["id"] == "OBR")
    set_field(obr, 7, "")
    set_field(obx_fields(payload)[0], 14, "")
    with pytest.raises(ConversionError) as raised:
        convert(payload)
    assert raised.value.code == "OBSERVATION_TIME_MISSING"


@pytest.mark.parametrize(
    ("hl7_status", "fhir_status"),
    [
        ("F", "final"),
        ("C", "corrected"),
        ("P", "preliminary"),
        ("X", "cancelled"),
        ("D", "entered-in-error"),
        ("W", "entered-in-error"),
        ("A", "amended"),
        ("I", "registered"),
    ],
)
def test_result_status_mapping(hl7_status: str, fhir_status: str) -> None:
    payload = example("oru_r01")
    set_field(obx_fields(payload)[0], 11, hl7_status)
    observation = convert(payload).observations[0]
    assert observation["status"] == fhir_status
    Observation.model_validate(observation)


def test_unmapped_result_status_is_an_error() -> None:
    payload = example("oru_r01")
    set_field(obx_fields(payload)[0], 11, "Q")
    with pytest.raises(ConversionError, match="OBX-11"):
        convert(payload)


def test_unknown_codes_and_text_values_are_skipped_and_counted() -> None:
    payload = example("oru_r01")
    fields = obx_fields(payload)
    fields[0][2] = [[["9999-9"], ["Unknown"], ["LN"]]]
    fields[1][1] = [[["ST"]]]
    fields[2][2] = [[["HR"], ["Local heart rate"], ["L"]]]
    converted = convert(payload)
    assert converted.skipped_observations == 3
    assert len(converted.observations) == 10


@pytest.mark.parametrize(
    ("pid8", "gender"),
    [("F", "female"), ("M", "male"), ("U", "unknown"), ("O", "other"), ("", "unknown")],
)
def test_gender_mapping(pid8: str, gender: str) -> None:
    payload = example("adt_a01")
    pid = next(s["fields"] for s in payload["segments"] if s["id"] == "PID")
    set_field(pid, 8, pid8)
    assert convert(payload).patient["gender"] == gender


def test_missing_visit_number_is_an_error() -> None:
    payload = example("adt_a01")
    pv1 = next(s["fields"] for s in payload["segments"] if s["id"] == "PV1")
    set_field(pv1, 19, "")
    with pytest.raises(ConversionError) as raised:
        convert(payload)
    assert raised.value.code == "VISIT_NUMBER_MISSING"


def test_missing_pid_is_an_error() -> None:
    payload = example("adt_a01")
    payload["segments"] = [s for s in payload["segments"] if s["id"] != "PID"]
    with pytest.raises(ConversionError) as raised:
        convert(payload)
    assert raised.value.code == "PID_MISSING"


def test_admission_is_in_progress_and_discharge_finished() -> None:
    assert convert(example("adt_a01")).encounter["status"] == "in-progress"
    discharge = convert(example("adt_a03_with_warning")).encounter
    assert discharge["status"] == "finished"
    assert discharge["period"]["end"] == "2024-03-17T09:15:00+00:00"


def test_patient_without_address_or_birth_date() -> None:
    payload = example("oru_r01")
    pid = next(s["fields"] for s in payload["segments"] if s["id"] == "PID")
    set_field(pid, 7, "")
    patient = convert(payload).patient
    assert "birthDate" not in patient
    assert "address" not in patient
    Patient.model_validate(patient)


def test_input_is_not_modified() -> None:
    payload = example("oru_r01")
    before = copy.deepcopy(payload)
    convert(payload)
    assert payload == before


@pytest.mark.parametrize(
    ("dtm", "expected"),
    [
        ("2024", "2024"),
        ("202403", "2024-03"),
        ("20240315", "2024-03-15"),
        ("2024031513", "2024-03-15T13:00:00+00:00"),
        ("202403151307", "2024-03-15T13:07:00+00:00"),
        ("20240315130705", "2024-03-15T13:07:05+00:00"),
        ("20240315130705.25", "2024-03-15T13:07:05.250000+00:00"),
        ("20240315130705-0500", "2024-03-15T13:07:05-05:00"),
        ("20240315130705+0530", "2024-03-15T13:07:05+05:30"),
    ],
)
def test_fhir_datetime(dtm: str, expected: str) -> None:
    assert fhir_datetime(dtm, "X") == expected


@pytest.mark.parametrize("dtm", ["", "2024-03-15", "20241", "yesterday"])
def test_fhir_datetime_rejects_non_dtm(dtm: str) -> None:
    with pytest.raises(ConversionError) as raised:
        fhir_datetime(dtm, "PID-7")
    assert raised.value.code == "TIMESTAMP_INVALID"


def test_fhir_date_drops_the_time() -> None:
    assert fhir_date("19580214083000", "PID-7") == "1958-02-14"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("MRN0001234", "MRN0001234"),
        ("7f1c2a90-1111-4a4a", "7f1c2a90-1111-4a4a"),
        ("A B/C", "A-B-C"),
        ("x" * 80, "x" * 64),
    ],
)
def test_fhir_id_sanitizes(text: str, expected: str) -> None:
    assert fhir_id(text) == expected


def test_fhir_id_rejects_empty() -> None:
    with pytest.raises(ConversionError):
        fhir_id("")
