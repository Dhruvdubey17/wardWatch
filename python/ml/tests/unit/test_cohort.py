from pathlib import Path

import pytest
from wardwatch_ml.cohort import SiteCohort, cohort_markdown, cohort_report
from wardwatch_ml.data import load_physionet

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "physionet"


@pytest.fixture(scope="module")
def reports() -> dict[str, SiteCohort]:
    return {report.site: report for report in cohort_report(load_physionet(FIXTURES))}


def test_counts_per_site(reports: dict[str, SiteCohort]) -> None:
    site_a = reports["A"]
    site_b = reports["B"]
    # Site A: p000001 and p000009 have an onset, p000002 is septic from row 0.
    assert (site_a.patients, site_a.septic_patients) == (9, 3)
    assert (site_b.patients, site_b.septic_patients) == (8, 3)
    assert site_a.septic_fraction == pytest.approx(3 / 9)
    # Hours are the row counts written by the fixture generator:
    # 40 + 24 + 30 + 48 + 22 + 36 + 28 + 26 + 44 = 298.
    assert site_a.hours == 298
    # Positive hours: p000001 rows 21-40 (20), p000002 all 24, p000009 rows 29-44 (16).
    assert site_a.positive_hours == 60


def test_missingness_fractions(reports: dict[str, SiteCohort]) -> None:
    missingness = reports["A"].missingness
    assert missingness["EtCO2"] == 1.0
    assert missingness["Age"] == 0.0
    assert 0.0 < missingness["HR"] < 0.2
    # Temperature is charted every fourth hour.
    assert 0.7 < missingness["Temp"] < 0.8


def test_markdown_has_one_row_per_site(reports: dict[str, SiteCohort]) -> None:
    text = cohort_markdown(list(reports.values()))
    assert text.count("\n") == 4
    assert "| A | 9 | 3 |" in text
