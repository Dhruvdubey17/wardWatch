import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from fhir_database import postgres_server
from fhir_kafka import kafka_server
from scorer_support import train_bundle
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from wardwatch_ml.bundle import ServingBundle


@pytest.fixture(scope="session")
def bundle(tmp_path_factory: pytest.TempPathFactory) -> ServingBundle:
    return train_bundle(tmp_path_factory.mktemp("bundle"))


@pytest.fixture(scope="session")
def postgres_server_url() -> Iterator[str]:
    with postgres_server() as url:
        yield url


@pytest.fixture(scope="session")
def kafka_bootstrap() -> Iterator[str]:
    with kafka_server() as bootstrap:
        yield bootstrap


@pytest.fixture
async def database_url(postgres_server_url: str) -> AsyncIterator[str]:
    name = f"scorer_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(postgres_server_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(postgres_server_url).set(database=name).render_as_string(hide_password=False)
    yield url
    async with admin.connect() as connection:
        await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    await admin.dispose()
