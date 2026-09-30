"""Per-site cohort description: patients, septic fraction, hours and missingness."""

from dataclasses import asdict, dataclass

import pandas as pd

from wardwatch_ml.data import DEMOGRAPHICS, LABEL, LABS, VITALS

MEASURED_VARIABLES = (*VITALS, *LABS)


@dataclass(frozen=True)
class SiteCohort:
    site: str
    patients: int
    septic_patients: int
    septic_fraction: float
    hours: int
    positive_hours: int
    median_hours_per_patient: float
    # Fraction of patient-hours with no value, per variable.
    missingness: dict[str, float]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def cohort_report(frame: pd.DataFrame) -> list[SiteCohort]:
    """Describe each site in a long-format table from wardwatch_ml.data."""
    reports = []
    for site, rows in frame.groupby("site", sort=True):
        by_patient = rows.groupby("patient_id")
        septic = by_patient[LABEL].max()
        variables = [*MEASURED_VARIABLES, *DEMOGRAPHICS]
        reports.append(
            SiteCohort(
                site=str(site),
                patients=int(septic.size),
                septic_patients=int(septic.sum()),
                septic_fraction=float(septic.mean()),
                hours=len(rows),
                positive_hours=int(rows[LABEL].sum()),
                median_hours_per_patient=float(by_patient.size().median()),
                missingness={name: float(rows[name].isna().mean()) for name in variables},
            )
        )
    return reports


def cohort_markdown(reports: list[SiteCohort]) -> str:
    """A compact table for summary.md; missingness is shown for the mapped variables only."""
    lines = [
        "| Site | Patients | Septic | Septic fraction | Patient-hours | Positive hours |",
        "|---|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {r.site} | {r.patients} | {r.septic_patients} | {r.septic_fraction:.3f} | "
        f"{r.hours} | {r.positive_hours} |"
        for r in reports
    )
    return "\n".join(lines) + "\n"
