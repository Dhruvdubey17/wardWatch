import json
from pathlib import Path

import hl7
import pytest
from wardwatch_sim.control_ids import ControlIdSequence
from wardwatch_sim.synthea import read_bundle
from wardwatch_sim.synthea_oru import convert_patient

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def convert(name: str) -> tuple[list[str], list[tuple[str, str]]]:
    patient = read_bundle(FIXTURES / "synthea" / name)
    converted = convert_patient(patient, ControlIdSequence("SYN"))
    return list(converted.messages), [(sign.loinc, sign.unit) for sign in converted.skipped]


def test_matches_golden_messages() -> None:
    messages, _ = convert("Ada432_Lindgren255_a1b2c3.json")
    golden = json.loads((FIXTURES / "golden" / "ada_synthea_messages.json").read_text())
    assert [message.split("\r")[:-1] for message in messages] == golden


def test_codes_outside_the_table_are_skipped() -> None:
    _, skipped = convert("Ada432_Lindgren255_a1b2c3.json")
    assert skipped == [("29463-7", "kg")]


def test_values_and_identity_survive_an_independent_parse() -> None:
    patient = read_bundle(FIXTURES / "synthea" / "Ada432_Lindgren255_a1b2c3.json")
    messages, _ = convert("Ada432_Lindgren255_a1b2c3.json")
    expected = {(sign.loinc, sign.value) for sign in patient.vital_signs if sign.loinc != "29463-7"}
    seen = set()
    for text in messages:
        message = hl7.parse(text)
        assert str(message.segment("PID")[3][0][0]) == patient.identity.mrn
        for obx in message.segments("OBX") if "ORU" in str(message.segment("MSH")[9]) else []:
            seen.add((str(obx[3][0][0]), float(str(obx[5]))))
    assert seen == expected


def test_messages_are_admit_results_discharge_in_time_order() -> None:
    messages, _ = convert("Jorge12_Garcia77_b2c3d4.json")
    kinds = [str(hl7.parse(text).segment("MSH")[9]) for text in messages]
    assert kinds == ["ADT^A01^ADT_A01", "ORU^R01^ORU_R01", "ADT^A03^ADT_A03"]


def test_patient_without_usable_vital_signs_yields_no_messages() -> None:
    messages, skipped = convert("Zoe5_Muller14_c3d4e5.json")
    assert messages == []
    assert skipped == []


def test_control_ids_are_unique_and_sequential() -> None:
    sequence = ControlIdSequence("SIM", width=4)
    assert [sequence.next() for _ in range(3)] == ["SIM0001", "SIM0002", "SIM0003"]
