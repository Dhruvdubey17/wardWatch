from datetime import UTC, datetime

import pytest
from wardwatch_sim.control_ids import ControlIdSequence, run_tag

pytestmark = pytest.mark.unit


def test_run_tag_is_base36_seconds() -> None:
    assert run_tag(datetime(1970, 1, 1, tzinfo=UTC)) == "0"
    assert run_tag(datetime(1970, 1, 1, 0, 0, 35, tzinfo=UTC)) == "Z"
    assert run_tag(datetime(1970, 1, 1, 0, 0, 36, tzinfo=UTC)) == "10"
    started = datetime(2026, 9, 30, 8, 0, tzinfo=UTC)
    assert int(run_tag(started), 36) == int(started.timestamp())


def test_runs_a_second_apart_never_share_control_ids() -> None:
    first = ControlIdSequence(f"SIM{run_tag(datetime(2026, 9, 30, 8, 0, 0, tzinfo=UTC))}")
    second = ControlIdSequence(f"SIM{run_tag(datetime(2026, 9, 30, 8, 0, 1, tzinfo=UTC))}")
    first_ids = {first.next() for _ in range(1000)}
    second_ids = {second.next() for _ in range(1000)}
    assert first_ids.isdisjoint(second_ids)


def test_tagged_control_ids_fit_msh10() -> None:
    # MSH-10 is an ST of at most 20 characters in HL7 v2.5.1.
    tag = run_tag(datetime(2099, 12, 31, tzinfo=UTC))
    assert len(ControlIdSequence(f"SIM{tag}").next()) <= 20
