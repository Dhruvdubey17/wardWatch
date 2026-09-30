"""Committed evaluation reports must be full-dataset runs, and the docs must cite one."""

import json
import re
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[3]
REPORTS = REPO / "ml" / "reports"
# PhysioNet 2019 training sets: training_setA has 20336 stays and training_setB 20000.
FULL_SITE_SIZES = {"A": 20336, "B": 20000}


def reports() -> list[Path]:
    return sorted(path.parent for path in REPORTS.glob("*/metrics.json"))


def metrics(run: Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((run / "metrics.json").read_text())
    return document


def test_there_is_at_least_one_report() -> None:
    assert reports()


@pytest.mark.parametrize("run", reports(), ids=lambda run: run.name)
def test_reports_are_full_dataset_runs_in_both_directions(run: Path) -> None:
    report = metrics(run)
    config = report["config"]
    assert config["smoke"] == "False"
    assert config["limit"] == "None"
    assert config["bootstrap_resamples"] == 1000
    assert set(report["directions"]) == {"A_to_B", "B_to_A"}
    for site, patients in FULL_SITE_SIZES.items():
        assert f"| {site} | {patients} |" in report["cohort_markdown"]
    assert len(report["leakage_checks"]) == 6
    assert all(report["leakage_checks"].values())


def test_the_docs_cite_a_committed_report() -> None:
    cited: set[str] = set()
    for document in (REPO / "README.md", REPO / "docs" / "evaluation.md"):
        cited.update(re.findall(r"ml/reports/([0-9TZ]+-[0-9a-f]+)/", document.read_text()))
    assert cited
    assert cited <= {run.name for run in reports()}
