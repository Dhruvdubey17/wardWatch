import base64
import json

import pytest
from jsonschema import Draft202012Validator
from wardwatch_fhir.payloads import deadletter_payload, observation_payload
from wardwatch_ml.contracts import contracts_dir

pytestmark = pytest.mark.unit


def validator(topic: str) -> Draft202012Validator:
    schema = json.loads((contracts_dir() / "schemas" / f"{topic}.schema.json").read_text())
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def test_deadletter_payload_matches_contract() -> None:
    payload = deadletter_payload(
        "VISIT_NUMBER_MISSING", "PV1-19 has no visit number", "C1", b"MSH|x\r"
    )
    assert list(validator("hl7.deadletter").iter_errors(payload)) == []
    assert payload["stage"] == "fhir"
    assert base64.b64decode(payload["raw_base64"]) == b"MSH|x\r"


def test_deadletter_payload_allows_unknown_control_id() -> None:
    payload = deadletter_payload("PAYLOAD_INVALID", "not json", None, b"\x00")
    assert payload["control_id"] is None
    assert list(validator("hl7.deadletter").iter_errors(payload)) == []


def test_observation_payload_matches_contract() -> None:
    example = json.loads(
        (contracts_dir() / "examples" / "fhir.observations" / "heart_rate.json").read_text()
    )
    payload = observation_payload(
        mrn=example["mrn"],
        encounter_id=example["encounter_id"],
        encounter_start=example["encounter_start"],
        message_time=example["message_time"],
        observation=example["observation"],
    )
    assert payload == example
    assert list(validator("fhir.observations").iter_errors(payload)) == []
