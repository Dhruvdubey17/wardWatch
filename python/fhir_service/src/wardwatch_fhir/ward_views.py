"""Read models for the dashboard: the ward census and one patient's vitals.

Clinical times in the simulator run ahead of the wall clock, so a vitals
window is anchored at the encounter's latest observation rather than now.
"""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession
from wardwatch_ml.contracts import loinc_by_code

from wardwatch_fhir.models import Alert, Encounter, Observation, Patient, Score

TILE_VITALS = {
    "8867-4": "heart_rate",
    "9279-1": "respiratory_rate",
    "59408-5": "spo2",
    "8480-6": "systolic_bp",
    "8310-5": "temperature",
}
TREND_HOURS = 12
UNRESOLVED = ("open", "acknowledged", "escalated")


def _score_view(score: Score) -> dict[str, Any]:
    return {
        "icu_hour": score.icu_hour,
        "hour_ending": score.hour_ending.isoformat(),
        "news2_total": score.news2_total,
        "news2": score.news2,
        "raw_score": score.raw_score,
        "calibrated_probability": score.calibrated_probability,
        "model_version": score.model_version,
        "alerted": score.alerted,
    }


async def census(session: AsyncSession) -> list[dict[str, Any]]:
    """One entry per in-progress encounter, ordered by bed."""
    rows = (
        await session.execute(
            select(Encounter, Patient)
            .join(Patient, Patient.id == Encounter.patient_id)
            .where(Encounter.status == "in-progress")
            .order_by(Encounter.bed, Encounter.id)
        )
    ).all()
    encounter_ids = [encounter.id for encounter, _ in rows]
    if not encounter_ids:
        return []

    latest = (
        await session.execute(
            select(
                Observation.encounter_id,
                Observation.code,
                Observation.value,
                Observation.unit,
                Observation.effective,
            )
            .where(Observation.encounter_id.in_(encounter_ids), Observation.code.in_(TILE_VITALS))
            .ext(distinct_on(Observation.encounter_id, Observation.code))
            .order_by(Observation.encounter_id, Observation.code, Observation.effective.desc())
        )
    ).all()
    vitals: dict[str, dict[str, Any]] = defaultdict(dict)
    for encounter_id, code, value, unit, effective in latest:
        vitals[encounter_id][TILE_VITALS[code]] = {
            "value": value,
            "unit": unit,
            "at": effective.isoformat(),
        }

    ranked = (
        select(
            Score,
            func.row_number()
            .over(partition_by=Score.encounter_id, order_by=Score.icu_hour.desc())
            .label("rank"),
        )
        .where(Score.encounter_id.in_(encounter_ids))
        .subquery()
    )
    recent_scores = (
        (
            await session.execute(
                select(Score)
                .join(
                    ranked,
                    and_(
                        Score.encounter_id == ranked.c.encounter_id,
                        Score.icu_hour == ranked.c.icu_hour,
                    ),
                )
                .where(ranked.c.rank <= TREND_HOURS)
                .order_by(Score.encounter_id, Score.icu_hour)
            )
        )
        .scalars()
        .all()
    )
    trends: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for score in recent_scores:
        trends[score.encounter_id].append(_score_view(score))

    open_counts = dict(
        (
            await session.execute(
                select(Alert.encounter_id, func.count())
                .where(Alert.encounter_id.in_(encounter_ids), Alert.status.in_(UNRESOLVED))
                .group_by(Alert.encounter_id)
            )
        ).all()
    )

    beds = []
    for encounter, patient in rows:
        trend = trends.get(encounter.id, [])
        beds.append(
            {
                "bed": encounter.bed,
                "encounter_id": encounter.id,
                "patient_id": patient.id,
                "mrn": patient.mrn,
                "name": {"family": patient.family, "given": patient.given},
                "admitted_at": encounter.period_start.isoformat()
                if encounter.period_start
                else None,
                "vitals": vitals.get(encounter.id, {}),
                "latest_score": trend[-1] if trend else None,
                "trend": trend,
                "unresolved_alerts": int(open_counts.get(encounter.id, 0)),
            }
        )
    return beds


async def patient_vitals(session: AsyncSession, mrn: str, hours: int) -> dict[str, Any] | None:
    """Observations, hourly scores and alerts for the patient's latest encounter."""
    patient = (
        await session.execute(select(Patient).where(Patient.mrn == mrn))
    ).scalar_one_or_none()
    if patient is None:
        return None
    encounter = (
        await session.execute(
            select(Encounter)
            .where(Encounter.patient_id == patient.id)
            .order_by(
                (Encounter.status == "in-progress").desc(),
                Encounter.period_start.desc().nulls_last(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if encounter is None:
        return {"mrn": mrn, "encounter_id": None, "series": {}, "scores": [], "alerts": []}

    newest: datetime | None = (
        await session.execute(
            select(func.max(Observation.effective)).where(Observation.encounter_id == encounter.id)
        )
    ).scalar_one()
    series: dict[str, dict[str, Any]] = {}
    scores: list[dict[str, Any]] = []
    window_start = None
    if newest is not None:
        window_start = newest - timedelta(hours=hours)
        observations = (
            (
                await session.execute(
                    select(Observation)
                    .where(
                        Observation.encounter_id == encounter.id,
                        Observation.effective >= window_start,
                    )
                    .order_by(Observation.effective, Observation.id)
                )
            )
            .scalars()
            .all()
        )
        codes = loinc_by_code()
        for observation in observations:
            entry = series.setdefault(
                observation.code,
                {
                    "display": codes[observation.code].display
                    if observation.code in codes
                    else observation.code,
                    "unit": observation.unit,
                    "points": [],
                },
            )
            entry["points"].append(
                {"at": observation.effective.isoformat(), "value": observation.value}
            )
        scores = [
            _score_view(score)
            for score in (
                await session.execute(
                    select(Score)
                    .where(Score.encounter_id == encounter.id, Score.hour_ending >= window_start)
                    .order_by(Score.icu_hour)
                )
            ).scalars()
        ]
    alerts = (
        (
            await session.execute(
                select(Alert).where(Alert.encounter_id == encounter.id).order_by(Alert.raised_at)
            )
        )
        .scalars()
        .all()
    )
    return {
        "mrn": mrn,
        "patient_id": patient.id,
        "name": {"family": patient.family, "given": patient.given},
        "encounter_id": encounter.id,
        "bed": encounter.bed,
        "admitted_at": encounter.period_start.isoformat() if encounter.period_start else None,
        "window": {
            "start": window_start.isoformat() if window_start else None,
            "end": newest.isoformat() if newest else None,
            "hours": hours,
        },
        "series": series,
        "scores": scores,
        "alerts": [
            {
                "id": alert.id,
                "icu_hour": alert.icu_hour,
                "raised_at": alert.raised_at.isoformat(),
                "source": alert.source,
                "status": alert.status,
            }
            for alert in alerts
        ],
    }
