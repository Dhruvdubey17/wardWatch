from collections.abc import AsyncIterator, Iterator

import pytest
from fhir_database import create_database, drop_database, postgres_server


@pytest.fixture(scope="session")
def postgres_server_url() -> Iterator[str]:
    with postgres_server() as url:
        yield url


@pytest.fixture
async def database_url(postgres_server_url: str) -> AsyncIterator[str]:
    url = await create_database(postgres_server_url)
    yield url
    await drop_database(postgres_server_url, url)
