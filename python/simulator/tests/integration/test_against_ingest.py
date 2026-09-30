import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sim_ingest import RunningIngest
from wardwatch_ml.contracts import loinc_by_code
from wardwatch_ml.data import read_stay
from wardwatch_sim.cli import Endpoint, LoadConfig, main, run_load
from wardwatch_sim.clock import SystemClock
from wardwatch_sim.control_ids import ControlIdSequence
from wardwatch_sim.faults import FaultInjector, load_fault_catalog
from wardwatch_sim.identities import IdentityAssigner
from wardwatch_sim.mllp import MllpClient, TcpTransport
from wardwatch_sim.synthea import load_patients
from wardwatch_sim.synthea_oru import convert_patient
from wardwatch_sim.ward import ScheduledMessage, Ward, replay, stay_from_frame

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
START = datetime(2024, 3, 15, 8, 0, tzinfo=UTC)


def fixture_stays() -> list[Any]:
    paths = sorted((FIXTURES / "physionet").glob("training_set*/*.psv"))
    return [stay_from_frame(read_stay(path, path.parent.name[-1])) for path in paths]


def identities() -> list[Any]:
    return [patient.identity for patient in load_patients(FIXTURES / "synthea")]


def client(ingest: RunningIngest) -> MllpClient:
    return MllpClient(connect=lambda: TcpTransport("127.0.0.1", ingest.port), ack_timeout=10.0)


def segment(record: dict[str, Any], segment_id: str) -> list[dict[str, Any]]:
    return [s for s in record["value"]["segments"] if s["id"] == segment_id]


def field_text(segment_fields: list[Any], number: int, component: int = 1) -> str:
    field = segment_fields[number - 1] if number - 1 < len(segment_fields) else []
    return str(field[0][component - 1][0]) if field else ""


def test_replay_counts_and_values_survive_the_round_trip(ingest: RunningIngest) -> None:
    stays = fixture_stays()
    ward = Ward(
        stays,
        beds=2,
        assigner=IdentityAssigner(identities(), 1),
        simulated_start=START,
        seconds_per_hour=0.0005,
    )
    sender = client(ingest)
    kinds: dict[str, str] = {}

    def send(message: ScheduledMessage, text: str) -> None:
        result = sender.send(text, message.control_id)
        assert result.ack.code == "AA", (message.control_id, result.ack)
        kinds[message.control_id] = message.kind

    count = replay(ward.schedule(), SystemClock(), send)
    sender.close()
    assert ingest.stop() == 0

    records = ingest.records("hl7.validated")
    assert len(records) == count
    assert ingest.records("hl7.deadletter") == []
    assert {r["value"]["control_id"] for r in records} == set(kinds)

    # Every non-missing mapped value in the .psv files arrives exactly once,
    # unchanged, at the right ICU hour of the right stay.
    codes = loinc_by_code()
    admitted: dict[str, datetime] = {}
    received: dict[tuple[str, int, str], float] = {}
    for record in records:
        pv1 = segment(record, "PV1")[0]["fields"]
        visit = field_text(pv1, 19)
        if record["value"]["message_type"] == "ADT" and record["value"]["trigger_event"] == "A01":
            admitted[visit] = datetime.strptime(field_text(pv1, 44), "%Y%m%d%H%M%S%z")
    for record in records:
        if record["value"]["message_type"] != "ORU":
            continue
        visit = field_text(segment(record, "PV1")[0]["fields"], 19)
        for obx in segment(record, "OBX"):
            fields = obx["fields"]
            observed = datetime.strptime(field_text(fields, 14), "%Y%m%d%H%M%S%z")
            hour = int((observed - admitted[visit]).total_seconds() // 3600)
            variable = codes[field_text(fields, 3)].physionet_variable
            key = (visit, hour, variable)
            assert key not in received
            received[key] = float(field_text(fields, 5))

    # Encounter IDs are handed out as stays leave the shared queue, so sorted
    # visit numbers line up with the stay order.
    assert len(admitted) == len(stays)
    expected: dict[tuple[str, int, str], float] = {}
    for stay, visit in zip(stays, sorted(admitted), strict=True):
        for row in stay.rows:
            for variable, number in row.values.items():
                expected[(visit, row.hour, variable)] = number
    assert received.keys() == expected.keys()
    for key, number in expected.items():
        assert math.isclose(received[key], number, rel_tol=0, abs_tol=0), key


@pytest.mark.parametrize("ingest", [["--max-frame-bytes", "4096"]], indirect=True)
def test_injected_faults_land_with_catalogued_codes(ingest: RunningIngest) -> None:
    catalog = {fault.name: fault for fault in load_fault_catalog()}
    rates = dict.fromkeys(catalog, 0.07)
    injector = FaultInjector(rates, seed=5, max_frame_bytes=4096)
    ward = Ward(
        fixture_stays(),
        beds=3,
        assigner=IdentityAssigner(identities(), 1),
        simulated_start=START,
        seconds_per_hour=0.0005,
    )
    sender = client(ingest)
    injected: list[tuple[str, str]] = []

    def send(message: ScheduledMessage, text: str) -> None:
        damaged, fault = injector.apply(text, message.control_id, message.kind)
        control_id = damaged.replace("\n", "\r").split("\r")[0].split("|")[9]
        result = sender.send(damaged, control_id, retry_on_ae=fault is None)
        if fault is None:
            assert result.ack.code == "AA"
            return
        assert result.ack.code == fault.ack_code, (fault.name, result.ack)
        injected.append((fault.name, control_id))

    replay(ward.schedule(), SystemClock(), send)
    sender.close()
    assert ingest.stop() == 0

    assert {name for name, _ in injected} == set(catalog), "every fault should have been injected"
    dead = defaultdict(list)
    for record in ingest.records("hl7.deadletter"):
        dead[record["value"]["control_id"]].append(record["value"])
    warned = defaultdict(set)
    for record in ingest.records("hl7.validated"):
        for warning in record["value"]["warnings"]:
            warned[record["value"]["control_id"]].add(warning["code"])
    for name, control_id in injected:
        fault = catalog[name]
        if fault.outcome == "error":
            matches = [d for d in dead[control_id] if d["error_code"] == fault.code]
            assert matches, (name, control_id, dead[control_id])
            assert matches[0]["stage"] == fault.stage
        else:
            assert fault.code in warned[control_id], (name, control_id)


def test_synthea_identities_and_values_round_trip(ingest: RunningIngest) -> None:
    sender = client(ingest)
    patients = load_patients(FIXTURES / "synthea")
    control_ids = ControlIdSequence("SYN")
    sent = 0
    for patient in patients:
        for text in convert_patient(patient, control_ids).messages:
            control_id = text.split("\r")[0].split("|")[9]
            assert sender.send(text, control_id).ack.code == "AA"
            sent += 1
    sender.close()
    assert ingest.stop() == 0
    records = ingest.records("hl7.validated")
    assert len(records) == sent
    by_mrn = {patient.identity.mrn: patient for patient in patients}
    for record in records:
        pid = segment(record, "PID")[0]["fields"]
        identity = by_mrn[field_text(pid, 3)].identity
        assert record["key"] == identity.mrn
        assert field_text(pid, 5, 1) == identity.family
        assert field_text(pid, 5, 2) == identity.given
        for obx in segment(record, "OBX"):
            signs = {(s.loinc, s.value) for s in by_mrn[identity.mrn].vital_signs}
            assert (field_text(obx["fields"], 3), float(field_text(obx["fields"], 5))) in signs


def test_load_mode_reports_throughput(ingest: RunningIngest, tmp_path: Path) -> None:
    output = tmp_path / "load.json"
    run_load(
        LoadConfig(
            Endpoint("127.0.0.1", ingest.port), rate=200, duration=1.5, connections=2, output=output
        )
    )
    assert ingest.stop() == 0
    report = json.loads(output.read_text())
    assert report["acks"].get("AA", 0) == report["acknowledged"] > 100
    assert report["throughput_per_second"] > 50
    assert set(report["ack_latency_ms"]) == {"p50", "p95", "p99", "max"}
    assert len(ingest.records("hl7.validated")) == report["acknowledged"]


def test_replay_command_end_to_end(ingest: RunningIngest, tmp_path: Path) -> None:
    report_path = tmp_path / "replay.json"
    code = main(
        [
            "replay",
            "--port",
            str(ingest.port),
            "--physionet-dir",
            str(FIXTURES / "physionet"),
            "--synthea-dir",
            str(FIXTURES / "synthea"),
            "--beds",
            "3",
            "--seconds-per-hour",
            "0.0005",
            "--fault",
            "missing_pid=0.05",
            "--report",
            str(report_path),
        ]
    )
    assert code == 0
    assert ingest.stop() == 0
    report = json.loads(report_path.read_text())
    assert report["undelivered"] == 0
    assert report["acks"]["AA"] == len(ingest.records("hl7.validated"))
    assert report["acks"].get("AE", 0) == report["faults"].get("missing_pid", 0)
    assert report["sent"] == sum(report["acks"].values())


def test_synthea_command_end_to_end(ingest: RunningIngest, tmp_path: Path) -> None:
    report_path = tmp_path / "synthea.json"
    assert (
        main(
            [
                "synthea",
                "--port",
                str(ingest.port),
                "--synthea-dir",
                str(FIXTURES / "synthea"),
                "--report",
                str(report_path),
            ]
        )
        == 0
    )
    assert ingest.stop() == 0
    report = json.loads(report_path.read_text())
    assert report["acks"] == {"AA": 7}
    assert len(ingest.records("hl7.validated")) == 7


def test_replay_without_identities_fails(tmp_path: Path) -> None:
    assert main(["replay", "--synthea-dir", str(tmp_path), "--port", "1"]) == 1
