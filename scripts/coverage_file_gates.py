"""Fail when a named source file's line coverage is below its own gate.

Reads a coverage.py JSON report. CLAUDE.md sets 90% for the FHIR converter and
the alert state machine on top of the per-package gates.
"""

import json
import sys
from pathlib import Path

FILE_GATES = {
    "fhir_service/src/wardwatch_fhir/converter.py": 90.0,
    "fhir_service/src/wardwatch_fhir/alert_states.py": 90.0,
    "fhir_service/src/wardwatch_fhir/alert_service.py": 90.0,
}


def failures(report: dict[str, object], gates: dict[str, float]) -> list[str]:
    files = report["files"]
    assert isinstance(files, dict)
    problems = []
    for path, gate in gates.items():
        entry = files.get(path)
        if entry is None:
            problems.append(f"{path}: not in the coverage report")
            continue
        percent = float(entry["summary"]["percent_covered"])
        if percent < gate:
            problems.append(f"{path}: {percent:.1f}% is below {gate:.0f}%")
    return problems


def main(argv: list[str]) -> int:
    report = json.loads(Path(argv[0]).read_text())
    problems = failures(report, FILE_GATES)
    for problem in problems:
        print(f"coverage_file_gates: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
