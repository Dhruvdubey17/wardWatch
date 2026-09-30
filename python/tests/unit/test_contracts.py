import base64
import json
from pathlib import Path
from typing import Any

import hl7
import pytest
from jsonschema import Draft202012Validator

pytestmark = pytest.mark.unit

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
TOPICS = [
    "hl7.validated",
    "hl7.deadletter",
    "fhir.observations",
    "ward.alerts",
    "ward.alert-events",
]


def load(path: Path) -> Any:
    return json.loads(path.read_text())


def validator(schema_name: str) -> Draft202012Validator:
    schema = load(CONTRACTS / "schemas" / f"{schema_name}.schema.json")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def example_paths() -> list[Path]:
    return sorted((CONTRACTS / "examples").glob("*/*.json"))


def test_every_topic_has_a_schema_and_an_example() -> None:
    for topic in TOPICS:
        assert (CONTRACTS / "schemas" / f"{topic}.schema.json").is_file()
        assert list((CONTRACTS / "examples" / topic).glob("*.json")), topic
    assert {path.parent.name for path in example_paths()} == set(TOPICS)


@pytest.mark.parametrize("path", example_paths(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_example_matches_its_topic_schema(path: Path) -> None:
    errors = list(validator(path.parent.name).iter_errors(load(path)))
    assert errors == [], [error.message for error in errors]


def test_schemas_reject_unknown_fields() -> None:
    example = load(CONTRACTS / "examples" / "ward.alerts" / "model.json")
    example["unexpected"] = 1
    assert not validator("ward.alerts").is_valid(example)


def test_fault_catalog_is_consistent() -> None:
    catalog = load(CONTRACTS / "fault_catalog.json")
    validator("fault_catalog").validate(catalog)
    names = [fault["name"] for fault in catalog["faults"]]
    assert len(names) == len(set(names))
    for fault in catalog["faults"]:
        if fault["outcome"] == "error":
            assert fault["code"] in catalog["error_codes"], fault["name"]
            assert fault["ack_code"] in {"AE", "AR"}, fault["name"]
        else:
            assert fault["code"] in catalog["warning_codes"], fault["name"]
            assert fault["ack_code"] == "AA", fault["name"]
    assert not set(catalog["error_codes"]) & set(catalog["warning_codes"])


def test_fault_catalog_covers_the_defects_in_the_brief() -> None:
    catalog = load(CONTRACTS / "fault_catalog.json")
    assert {fault["name"] for fault in catalog["faults"]} == {
        "truncated_message",
        "missing_pid",
        "wrong_encoding_characters",
        "lf_segment_terminators",
        "non_numeric_nm",
        "invalid_timestamp",
        "duplicate_control_id",
        "trailing_separators",
        "non_ascii_bytes",
        "oversized_frame",
    }


def test_loinc_table_is_valid_and_unique() -> None:
    table = load(CONTRACTS / "loinc_codes.json")
    validator("loinc_codes").validate(table)
    codes = [entry["code"] for entry in table["codes"]]
    variables = [entry["physionet_variable"] for entry in table["codes"]]
    assert len(codes) == len(set(codes))
    assert len(variables) == len(set(variables))
    for entry in table["codes"]:
        assert entry["plausible_min"] < entry["plausible_max"], entry["code"]


REQUIRED_SEGMENTS = {
    "adt_a01.hl7": ["MSH", "EVN", "PID", "PV1"],
    "adt_a03.hl7": ["MSH", "EVN", "PID", "PV1"],
    "oru_r01.hl7": ["MSH", "PID", "PV1", "OBR", "OBX"],
}


@pytest.mark.parametrize("name", sorted(REQUIRED_SEGMENTS))
def test_hl7_sample_parses_with_independent_parser(name: str) -> None:
    raw = (CONTRACTS / "hl7" / name).read_bytes()
    assert b"\n" not in raw
    assert raw.endswith(b"\r")
    message = hl7.parse(raw.decode("ascii"))
    segment_ids = [str(segment[0]) for segment in message]
    for required in REQUIRED_SEGMENTS[name]:
        assert required in segment_ids, required
    assert str(message.segment("PID")[3][0][0]) == "MRN0001234"


def test_oru_sample_uses_only_known_codes_and_units() -> None:
    table = {entry["code"]: entry for entry in load(CONTRACTS / "loinc_codes.json")["codes"]}
    message = hl7.parse((CONTRACTS / "hl7" / "oru_r01.hl7").read_bytes().decode("ascii"))
    obx_segments = message.segments("OBX")
    assert len(obx_segments) == len(table)
    for obx in obx_segments:
        code = str(obx[3][0][0])
        assert code in table
        assert str(obx[3][0][1]) == table[code]["display"]
        assert str(obx[6][0][0]) == table[code]["ucum_unit"]
        assert str(obx[11]) == "F"


@pytest.mark.parametrize("name", ["oru_r01", "adt_a01"])
def test_validated_example_carries_the_sample_bytes(name: str) -> None:
    example = load(CONTRACTS / "examples" / "hl7.validated" / f"{name}.json")
    raw = (CONTRACTS / "hl7" / f"{name}.hl7").read_bytes()
    assert base64.b64decode(example["raw_base64"]) == raw
    assert len(example["segments"]) == raw.count(b"\r")
