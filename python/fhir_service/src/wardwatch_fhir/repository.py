"""Idempotent writes of converted resources.

Every upsert updates a row only when the stored resource differs, so replaying
a message (Kafka delivers at least once) leaves the database exactly as it was.
ADT messages own patient demographics and encounter state; a result message
creates a missing patient or encounter but never overwrites one, and never
reopens a finished encounter.
"""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wardwatch_fhir.converter import Converted, parse_fhir_datetime
from wardwatch_fhir.models import Encounter, Observation, Patient


def _now() -> datetime:
    return datetime.now(UTC)


def _patient_row(resource: dict[str, Any]) -> dict[str, Any]:
    name = resource["name"][0]
    birth = resource.get("birthDate")
    birth_date = date.fromisoformat(birth) if birth and len(birth) == 10 else None
    return {
        "id": resource["id"],
        "mrn": resource["identifier"][0]["value"],
        "family": name.get("family", ""),
        "given": " ".join(name.get("given", [])),
        "birth_date": birth_date,
        "gender": resource["gender"],
        "resource": resource,
        "updated_at": _now(),
    }


def _encounter_row(resource: dict[str, Any]) -> dict[str, Any]:
    period = resource.get("period", {})
    location = resource.get("location", [{}])[0].get("location", {}).get("display", "")
    return {
        "id": resource["id"],
        "patient_id": resource["subject"]["reference"].removeprefix("Patient/"),
        "status": resource["status"],
        "bed": location,
        "period_start": parse_fhir_datetime(period["start"]) if "start" in period else None,
        "period_end": parse_fhir_datetime(period["end"]) if "end" in period else None,
        "resource": resource,
        "updated_at": _now(),
    }


def _observation_row(resource: dict[str, Any], message_time: str) -> dict[str, Any]:
    quantity = resource.get("valueQuantity", {})
    return {
        "id": resource["id"],
        "patient_id": resource["subject"]["reference"].removeprefix("Patient/"),
        "encounter_id": resource["encounter"]["reference"].removeprefix("Encounter/"),
        "code": resource["code"]["coding"][0]["code"],
        "category": resource["category"][0]["coding"][0]["code"],
        "status": resource["status"],
        "effective": parse_fhir_datetime(resource["effectiveDateTime"]),
        "value": quantity.get("value"),
        "unit": quantity.get("unit"),
        "message_time": message_time,
        "resource": resource,
    }


async def upsert_patient(
    session: AsyncSession, resource: dict[str, Any], authoritative: bool
) -> None:
    row = _patient_row(resource)
    statement = insert(Patient).values(row)
    if authoritative:
        changed = {key: statement.excluded[key] for key in row if key != "id"}
        statement = statement.on_conflict_do_update(
            index_elements=[Patient.id],
            set_=changed,
            where=Patient.resource.is_distinct_from(statement.excluded.resource),
        )
    else:
        statement = statement.on_conflict_do_nothing(index_elements=[Patient.id])
    await session.execute(statement)


async def upsert_encounter(
    session: AsyncSession, resource: dict[str, Any], trigger_event: str
) -> None:
    row = _encounter_row(resource)
    statement = insert(Encounter).values(row)
    if trigger_event in ("A01", "A03"):
        changed = {key: statement.excluded[key] for key in row if key != "id"}
        statement = statement.on_conflict_do_update(
            index_elements=[Encounter.id],
            set_=changed,
            where=Encounter.resource.is_distinct_from(statement.excluded.resource),
        )
    else:
        statement = statement.on_conflict_do_nothing(index_elements=[Encounter.id])
    await session.execute(statement)


async def upsert_observations(
    session: AsyncSession, resources: list[dict[str, Any]], message_time: str
) -> None:
    if not resources:
        return
    rows = [_observation_row(resource, message_time) for resource in resources]
    statement = insert(Observation).values(rows)
    changed = {key: statement.excluded[key] for key in rows[0] if key != "id"}
    statement = statement.on_conflict_do_update(
        index_elements=[Observation.id],
        set_=changed,
        where=Observation.resource.is_distinct_from(statement.excluded.resource),
    )
    await session.execute(statement)


async def store_converted(session: AsyncSession, converted: Converted) -> None:
    """Write one message's resources; the caller owns the transaction."""
    authoritative = converted.message_type == "ADT"
    await upsert_patient(session, converted.patient, authoritative=authoritative)
    await upsert_encounter(session, converted.encounter, converted.trigger_event)
    await upsert_observations(session, converted.observations, converted.message_time)


async def encounter_start(session: AsyncSession, encounter_id: str) -> datetime | None:
    result = await session.execute(
        select(Encounter.period_start).where(Encounter.id == encounter_id)
    )
    return result.scalar_one_or_none()
