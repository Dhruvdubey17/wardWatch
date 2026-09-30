from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import hl7
import pytest
from wardwatch_ml.data import read_stay
from wardwatch_sim.clock import FakeClock
from wardwatch_sim.hl7 import PatientIdentity
from wardwatch_sim.identities import IdentityAssigner
from wardwatch_sim.ward import HourRow, ScheduledMessage, Stay, Ward, replay, stay_from_frame

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
START = datetime(2024, 3, 15, 8, 0, tzinfo=UTC)


def pool(size: int = 6) -> list[PatientIdentity]:
    return [
        PatientIdentity(
            f"MRN{i:03d}", f"Family{i}", f"Given{i}", date(1950 + i, 1, 1), "F" if i % 2 else "M"
        )
        for i in range(size)
    ]


def short_stay(patient_id: str, hours: int) -> Stay:
    rows = tuple(HourRow(hour, {"HR": 80.0 + hour, "Resp": 16.0}) for hour in range(1, hours + 1))
    return Stay("A", patient_id, 60.0, "M", rows)


def test_stay_from_frame_keeps_only_mapped_non_missing_values() -> None:
    stay = stay_from_frame(read_stay(FIXTURES / "physionet" / "training_setA" / "p000001.psv", "A"))
    assert stay.key == "A/p000001"
    assert stay.gender == "M"
    assert stay.age == pytest.approx(71.2)
    assert len(stay.rows) == 40
    assert stay.rows[0].hour == 1
    assert "EtCO2" not in stay.rows[0].values
    assert "Glucose" not in stay.rows[0].values
    assert stay.rows[0].values["HR"] == pytest.approx(81.66)
    assert "Temp" not in stay.rows[1].values


def test_one_bed_admits_results_then_discharges() -> None:
    ward = Ward(
        [short_stay("p1", 3)], beds=1, assigner=IdentityAssigner(pool(), 1), simulated_start=START
    )
    messages = list(ward.schedule())
    assert [m.kind for m in messages] == ["admit", "result", "result", "result", "discharge"]
    assert [m.simulated_at - START for m in messages] == [
        timedelta(0),
        timedelta(hours=1),
        timedelta(hours=2),
        timedelta(hours=3),
        timedelta(hours=3, minutes=30),
    ]
    assert [m.due_seconds for m in messages] == [0.0, 2.0, 4.0, 6.0, 7.0]
    assert len({m.control_id for m in messages}) == 5


def test_next_stay_follows_discharge_and_turnover_in_the_same_bed() -> None:
    ward = Ward(
        [short_stay("p1", 2), short_stay("p2", 1)],
        beds=1,
        assigner=IdentityAssigner(pool(), 1),
        simulated_start=START,
    )
    messages = list(ward.schedule())
    second_admit = [m for m in messages if m.kind == "admit"][1]
    # 2 hours of rows, 30 minutes to discharge, one hour of turnover.
    assert second_admit.simulated_at - START == timedelta(hours=3, minutes=30)
    assert second_admit.stay_key == "A/p2"


def test_beds_run_concurrently_and_merge_in_time_order() -> None:
    stays = [short_stay(f"p{i}", 2 + i) for i in range(4)]
    ward = Ward(stays, beds=2, assigner=IdentityAssigner(pool(), 1), simulated_start=START)
    messages = list(ward.schedule())
    dues = [m.due_seconds for m in messages]
    assert dues == sorted(dues)
    assert {m.bed for m in messages} == {"01", "02"}
    assert sum(m.kind == "admit" for m in messages) == 4


def test_concurrent_beds_never_share_an_identity() -> None:
    stays = [short_stay(f"p{i}", 3 + (i % 3)) for i in range(12)]
    ward = Ward(stays, beds=3, assigner=IdentityAssigner(pool(4), 1), simulated_start=START)
    active: dict[str, str] = {}
    for message in ward.schedule():
        if message.kind == "admit":
            assert message.mrn not in active.values()
            active[message.bed] = message.mrn
        elif message.kind == "discharge":
            del active[message.bed]


def test_schedule_is_deterministic() -> None:
    def run() -> list[tuple[str, str, str, float]]:
        stays = [short_stay(f"p{i}", 2 + i % 4) for i in range(8)]
        ward = Ward(stays, beds=3, assigner=IdentityAssigner(pool(), 9), simulated_start=START)
        return [(m.control_id, m.mrn, m.bed, m.due_seconds) for m in ward.schedule()]

    assert run() == run()


def test_hours_without_measurements_send_nothing() -> None:
    stay = Stay("A", "p1", None, None, (HourRow(1, {}), HourRow(2, {"HR": 70.0})))
    ward = Ward([stay], beds=1, assigner=IdentityAssigner(pool(), 1), simulated_start=START)
    assert [m.kind for m in ward.schedule()] == ["admit", "result", "discharge"]


def test_rendered_result_uses_send_time_in_msh7_and_simulated_time_in_obx14() -> None:
    ward = Ward(
        [short_stay("p1", 1)], beds=1, assigner=IdentityAssigner(pool(), 1), simulated_start=START
    )
    result = next(m for m in ward.schedule() if m.kind == "result")
    message = hl7.parse(result.render(datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)))
    assert str(message.segment("MSH")[7]) == "20260101120000+0000"
    assert str(message.segment("OBR")[7]) == "20240315090000+0000"
    observations = {str(obx[3][0][0]): str(obx[5]) for obx in message.segments("OBX")}
    assert observations == {"8867-4": "81", "9279-1": "16"}


def test_replay_sleeps_until_each_message_is_due() -> None:
    ward = Ward(
        [short_stay("p1", 2)],
        beds=1,
        assigner=IdentityAssigner(pool(), 1),
        simulated_start=START,
        seconds_per_hour=10.0,
    )
    clock = FakeClock()
    sent: list[tuple[str, float]] = []

    def send(message: ScheduledMessage, text: str) -> None:
        sent.append((message.kind, clock.monotonic()))
        assert text.startswith("MSH|")

    count = replay(ward.schedule(), clock, send)
    assert count == 4
    assert sent == [("admit", 0.0), ("result", 10.0), ("result", 20.0), ("discharge", 25.0)]
    assert clock.sleeps == [10.0, 10.0, 5.0]


@pytest.mark.parametrize(("beds", "seconds"), [(0, 2.0), (1, 0.0), (1, -1.0)])
def test_rejects_invalid_ward(beds: int, seconds: float) -> None:
    with pytest.raises(ValueError, match=r"bed|positive"):
        Ward(
            [],
            beds=beds,
            assigner=IdentityAssigner(pool(), 1),
            simulated_start=START,
            seconds_per_hour=seconds,
        )
