import asyncio
import copy
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from wardwatch_fhir.converter import convert
from wardwatch_fhir.db import create_engine, session_factory
from wardwatch_fhir.migrate import upgrade
from wardwatch_fhir.models import Encounter, Observation, Patient
from wardwatch_fhir.repository import store_converted
from wardwatch_ml.contracts import contracts_dir

pytestmark = pytest.mark.integration

EXAMPLES = contracts_dir() / "examples" / "hl7.validated"


def example(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((EXAMPLES / f"{name}.json").read_text())
    return loaded


@pytest.fixture
async def sessions(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    await asyncio.to_thread(upgrade, database_url)
    engine = create_engine(database_url)
    yield session_factory(engine)
    await engine.dispose()


async def store(sessions: async_sessionmaker[AsyncSession], payload: dict[str, Any]) -> None:
    async with sessions.begin() as session:
        await store_converted(session, convert(payload))


async def snapshot(sessions: async_sessionmaker[AsyncSession]) -> dict[str, list[tuple[Any, ...]]]:
    async with sessions() as session:
        tables = {}
        for table in ("patients", "encounters", "observations"):
            rows = await session.execute(text(f"SELECT * FROM {table} ORDER BY id"))
            tables[table] = [tuple(row) for row in rows]
        return tables


async def test_replaying_a_message_changes_nothing(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await store(sessions, example("adt_a01"))
    await store(sessions, example("oru_r01"))
    before = await snapshot(sessions)
    await store(sessions, example("oru_r01"))
    await store(sessions, example("adt_a01"))
    assert await snapshot(sessions) == before
    assert len(before["observations"]) == 13


async def test_results_do_not_overwrite_admission_demographics(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await store(sessions, example("adt_a01"))
    await store(sessions, example("oru_r01"))
    async with sessions() as session:
        patient = (await session.execute(select(Patient))).scalar_one()
    assert patient.resource["address"][0]["city"] == "Boston"


async def test_result_before_admission_creates_patient_and_encounter(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await store(sessions, example("oru_r01"))
    async with sessions() as session:
        encounter = (await session.execute(select(Encounter))).scalar_one()
        count = len((await session.execute(select(Observation))).scalars().all())
    assert encounter.status == "in-progress"
    assert count == 13
    # The later admission fills in what the result message lacked.
    await store(sessions, example("adt_a01"))
    async with sessions() as session:
        encounter = (await session.execute(select(Encounter))).scalar_one()
        patient = (await session.execute(select(Patient))).scalar_one()
    assert encounter.period_start is not None
    assert "address" in patient.resource


async def test_late_result_does_not_reopen_a_discharge(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await store(sessions, example("adt_a01"))
    await store(sessions, example("adt_a03_with_warning"))
    await store(sessions, example("oru_r01"))
    async with sessions() as session:
        encounter = (await session.execute(select(Encounter))).scalar_one()
    assert encounter.status == "finished"
    assert encounter.period_end is not None


async def test_corrected_result_replaces_the_value(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await store(sessions, example("oru_r01"))
    corrected = copy.deepcopy(example("oru_r01"))
    first_obx = next(s["fields"] for s in corrected["segments"] if s["id"] == "OBX")
    first_obx[4] = [[["118"]]]
    first_obx[10] = [[["C"]]]
    await store(sessions, corrected)
    async with sessions() as session:
        row = (
            await session.execute(select(Observation).where(Observation.id == "SIM000007-1"))
        ).scalar_one()
    assert row.value == 118.0
    assert row.status == "corrected"
