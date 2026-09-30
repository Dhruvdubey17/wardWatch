"""A fresh Postgres database per test, from a local server or testcontainers."""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.community.postgres import PostgresContainer

POSTGRES_ENV = "WARDWATCH_TEST_POSTGRES_URL"


@contextmanager
def postgres_server() -> Iterator[str]:
    """Yield an asyncpg SQLAlchemy URL for a server we may create databases on."""
    configured = os.environ.get(POSTGRES_ENV)
    if configured:
        yield configured
        return
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        yield container.get_connection_url()


async def _administer(server_url: str, statement: str) -> None:
    # CREATE and DROP DATABASE cannot run inside a transaction.
    engine = create_async_engine(server_url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as connection:
        await connection.execute(text(statement))
    await engine.dispose()


async def create_database(server_url: str) -> str:
    name = f"wardwatch_{uuid.uuid4().hex[:12]}"
    await _administer(server_url, f'CREATE DATABASE "{name}"')
    return make_url(server_url).set(database=name).render_as_string(hide_password=False)


async def drop_database(server_url: str, database_url: str) -> None:
    name = make_url(database_url).database
    await _administer(server_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
