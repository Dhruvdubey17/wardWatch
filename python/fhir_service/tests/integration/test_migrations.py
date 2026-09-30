import asyncio
from datetime import UTC, datetime

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import insert, inspect, text
from sqlalchemy.engine import Connection
from wardwatch_fhir.db import create_engine
from wardwatch_fhir.migrate import downgrade, upgrade
from wardwatch_fhir.models import Alert, AlertEvent, Base

pytestmark = pytest.mark.integration

TABLES = {"patients", "encounters", "observations", "alerts", "alert_events", "scores"}


async def table_names(database_url: str) -> set[str]:
    engine = create_engine(database_url)
    async with engine.connect() as connection:
        names = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
    await engine.dispose()
    return names - {"alembic_version"}


async def test_upgrade_downgrade_upgrade(database_url: str) -> None:
    await asyncio.to_thread(upgrade, database_url)
    assert await table_names(database_url) == TABLES
    await asyncio.to_thread(downgrade, database_url)
    assert await table_names(database_url) == set()
    await asyncio.to_thread(upgrade, database_url)
    assert await table_names(database_url) == TABLES


async def test_models_match_the_migration(database_url: str) -> None:
    await asyncio.to_thread(upgrade, database_url)
    engine = create_engine(database_url)

    def differences(connection: Connection) -> list[object]:
        context = MigrationContext.configure(connection)
        return list(compare_metadata(context, Base.metadata))

    async with engine.connect() as connection:
        diff = await connection.run_sync(differences)
    await engine.dispose()
    assert diff == []


async def test_alert_events_are_append_only(database_url: str) -> None:
    await asyncio.to_thread(upgrade, database_url)
    engine = create_engine(database_url)
    now = datetime.now(UTC)
    async with engine.begin() as connection:
        await connection.execute(
            insert(Alert).values(
                id="a1",
                mrn="M1",
                encounter_id="E1",
                source="news2",
                raised_at=now,
                icu_hour=5,
                raw_score=6,
                news2={},
                top_factors=[],
                model_version="news2-rcp-2017",
                message_time="t",
                status="open",
                version=1,
                updated_at=now,
            )
        )
        await connection.execute(
            insert(AlertEvent).values(
                alert_id="a1",
                transition="acknowledge",
                from_status="open",
                to_status="acknowledged",
                actor="Dr A",
                occurred_at=now,
            )
        )
    for statement in ("UPDATE alert_events SET actor = 'someone else'", "DELETE FROM alert_events"):
        async with engine.begin() as connection:
            with pytest.raises(Exception, match="append-only"):
                await connection.execute(text(statement))
    await engine.dispose()
