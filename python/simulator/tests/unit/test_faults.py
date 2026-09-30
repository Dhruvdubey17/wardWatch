from collections import Counter
from datetime import UTC, date, datetime

import hl7
import pytest
from wardwatch_ml.contracts import contracts_dir
from wardwatch_sim.faults import (
    FaultInjector,
    load_fault_catalog,
    parse_rates,
)
from wardwatch_sim.hl7 import MessageHeader, PatientIdentity, Visit, build_adt_a01

pytestmark = pytest.mark.unit

ORU = (contracts_dir() / "hl7" / "oru_r01.hl7").read_bytes().decode("ascii")
ADMITTED = datetime(2024, 3, 15, 8, 30, tzinfo=UTC)


def adt() -> str:
    patient = PatientIdentity("M1", "Lindgren", "Ada", date(1958, 2, 14), "F")
    return build_adt_a01(MessageHeader("A1", ADMITTED), patient, Visit("E1", "01", "A", ADMITTED))


def only(name: str, max_frame_bytes: int = 1 << 20) -> FaultInjector:
    return FaultInjector({name: 1.0}, seed=1, max_frame_bytes=max_frame_bytes)


def test_catalog_has_every_fault_from_the_brief() -> None:
    assert len(load_fault_catalog()) == 10


def test_truncated_message_keeps_msh_and_half_of_pid() -> None:
    text, fault = only("truncated_message").apply(ORU, "C1", "result")
    assert fault is not None
    assert fault.code == "REQUIRED_SEGMENT_MISSING"
    segments = text.split("\r")
    assert segments[0] == ORU.split("\r")[0]
    assert len(segments) == 2
    assert ORU.split("\r")[1].startswith(segments[1])
    assert "OBX" not in text


def test_missing_pid_removes_only_pid() -> None:
    text, _ = only("missing_pid").apply(ORU, "C1", "result")
    assert "PID|" not in text
    assert text.count("\r") == ORU.count("\r") - 1


def test_wrong_encoding_characters() -> None:
    text, fault = only("wrong_encoding_characters").apply(ORU, "C1", "result")
    assert text.startswith("MSH|^^\\&|")
    assert fault is not None
    assert fault.ack_code == "AR"


def test_lf_terminators() -> None:
    text, fault = only("lf_segment_terminators").apply(ORU, "C1", "result")
    assert "\r" not in text
    assert text.count("\n") == ORU.count("\r")
    assert fault is not None
    assert fault.outcome == "warning"


def test_non_numeric_nm_changes_first_numeric_value() -> None:
    text, _ = only("non_numeric_nm").apply(ORU, "C1", "result")
    first = hl7.parse(text).segments("OBX")[0]
    assert str(first[5]) == "high"
    assert str(first[2]) == "NM"


def test_non_numeric_nm_is_not_applied_to_admissions() -> None:
    text, fault = only("non_numeric_nm").apply(adt(), "C1", "admit")
    assert fault is None
    assert text == adt()


def test_invalid_timestamp_sets_month_thirteen() -> None:
    text, _ = only("invalid_timestamp").apply(ORU, "C1", "result")
    assert str(hl7.parse(text).segment("MSH")[7]) == "20241315130005+0000"


def test_trailing_separators_on_every_segment() -> None:
    text, _ = only("trailing_separators").apply(ORU, "C1", "result")
    assert all(segment.endswith("||") for segment in text.split("\r") if segment)


def test_non_ascii_bytes_in_family_name_without_charset() -> None:
    text, _ = only("non_ascii_bytes").apply(ORU, "C1", "result")
    assert "Lindgrené^Ada" in text
    assert "UNICODE" not in text


def test_non_ascii_fault_removes_a_declared_character_set() -> None:
    declared = ORU.replace("|2.5.1\r", "|2.5.1||||||UNICODE UTF-8\r", 1)
    text, _ = only("non_ascii_bytes").apply(declared, "C1", "result")
    assert "UNICODE" not in text
    assert text.split("\r")[0].endswith("|2.5.1")
    assert "Lindgrené" in text


def test_oversized_frame_exceeds_the_limit() -> None:
    text, fault = only("oversized_frame", max_frame_bytes=2048).apply(ORU, "C1", "result")
    assert len(text.encode()) > 2048
    assert text.startswith(ORU)
    assert fault is not None
    assert fault.stage == "frame"


def test_duplicate_reuses_the_last_accepted_control_id() -> None:
    injector = FaultInjector({"duplicate_control_id": 1.0}, seed=1)
    # Nothing accepted yet, so the first message cannot be a duplicate.
    first, fault = injector.apply(ORU, "SIM000007", "result")
    assert fault is None
    assert first == ORU
    second_source = ORU.replace("|SIM000007|", "|SIM000008|")
    second, fault = injector.apply(second_source, "SIM000008", "result")
    assert fault is not None
    assert fault.code == "CONTROL_ID_DUPLICATE"
    assert str(hl7.parse(second).segment("MSH")[10]) == "SIM000007"


def test_rejected_messages_are_not_reused_as_duplicates() -> None:
    injector = FaultInjector({"duplicate_control_id": 0.5, "missing_pid": 0.5}, seed=4)
    accepted: list[str] = []
    for index in range(200):
        control_id = f"C{index}"
        text, fault = injector.apply(
            ORU.replace("|SIM000007|", f"|{control_id}|"), control_id, "result"
        )
        if fault is None:
            accepted.append(control_id)
        elif fault.name == "duplicate_control_id":
            assert str(hl7.parse(text).segment("MSH")[10]) in accepted


def test_every_catalogued_fault_has_a_transform() -> None:
    rates = {fault.name: 0.1 for fault in load_fault_catalog()}
    FaultInjector(rates, seed=1)


def test_rates_are_respected_for_a_fixed_seed() -> None:
    rates = {"missing_pid": 0.05, "lf_segment_terminators": 0.10, "invalid_timestamp": 0.02}
    injector = FaultInjector(rates, seed=2019)
    trials = 20_000
    counts: Counter[str] = Counter()
    for index in range(trials):
        _, fault = injector.apply(ORU, f"C{index}", "result")
        counts[fault.name if fault else "none"] += 1
    for name, rate in rates.items():
        expected = rate * trials
        # Four binomial standard deviations: a fixed seed never flakes, and a
        # rate off by a quarter would still fail.
        tolerance = 4 * (trials * rate * (1 - rate)) ** 0.5
        assert abs(counts[name] - expected) < tolerance, name


def test_same_seed_damages_the_same_messages() -> None:
    def run() -> list[str | None]:
        injector = FaultInjector({"missing_pid": 0.3, "trailing_separators": 0.3}, seed=11)
        faults = [injector.apply(ORU, f"C{i}", "result")[1] for i in range(100)]
        return [fault.name if fault else None for fault in faults]

    assert run() == run()


@pytest.mark.parametrize(
    ("rates", "message"),
    [
        ({"no_such_fault": 0.1}, "unknown"),
        ({"missing_pid": 0.7, "truncated_message": 0.4}, "sum"),
        ({"missing_pid": -0.1}, "non-negative"),
    ],
)
def test_invalid_rates_are_rejected(rates: dict[str, float], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        FaultInjector(rates, seed=1)


def test_parse_rates() -> None:
    assert parse_rates(["missing_pid=0.1", "lf_segment_terminators=0.02"]) == {
        "missing_pid": 0.1,
        "lf_segment_terminators": 0.02,
    }
    with pytest.raises(ValueError, match="name=rate"):
        parse_rates(["missing_pid"])
