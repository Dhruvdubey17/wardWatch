"""Alembic environment: runs migrations over the async engine."""

import asyncio

from alembic import context
from sqlalchemy.engine import Connection
from wardwatch_fhir.db import create_engine
from wardwatch_fhir.models import Base
from wardwatch_fhir.settings import Settings


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _migrate(url: str) -> None:
    engine = create_engine(url)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


url = context.config.get_main_option("sqlalchemy.url") or Settings().database_url
asyncio.run(_migrate(url))
