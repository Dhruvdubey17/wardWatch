import json
from pathlib import Path

import coverage_file_gates
import pytest
from coverage_file_gates import failures

pytestmark = pytest.mark.unit


def report(**percent: float) -> dict[str, object]:
    return {
        "files": {path: {"summary": {"percent_covered": value}} for path, value in percent.items()}
    }


def test_passes_at_or_above_the_gate() -> None:
    assert failures(report(a=90.0, b=99.5), {"a": 90.0, "b": 90.0}) == []


def test_reports_low_and_missing_files() -> None:
    assert failures(report(a=89.9), {"a": 90.0, "b": 90.0}) == [
        "a: 89.9% is below 90%",
        "b: not in the coverage report",
    ]


def test_main_exit_code(tmp_path: Path) -> None:
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(report(**dict.fromkeys(coverage_file_gates.FILE_GATES, 100.0))))
    assert coverage_file_gates.main([str(path)]) == 0
    path.write_text(json.dumps(report()))
    assert coverage_file_gates.main([str(path)]) == 1
