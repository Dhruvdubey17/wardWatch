import asyncio
import copy
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.capabilitystatement import CapabilityStatement
from fhir.resources.R4B.operationoutcome import OperationOutcome
from wardwatch_fhir.app import create_app
from wardwatch_fhir.converter import convert
from wardwatch_fhir.db import create_engine, session_factory
from wardwatch_fhir.migrate import upgrade
from wardwatch_fhir.repository import store_converted
from wardwatch_fhir.settings import Settings
from wardwatch_ml.contracts import contracts_dir

pytestmark = pytest.mark.integration

EXAMPLES = contracts_dir() / "examples" / "hl7.validated"
BASE = "http://testserver"


def example(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((EXAMPLES / f"{name}.json").read_text())
    return loaded


def later_oru(control_id: str, timestamp: str) -> dict[str, Any]:
    """The contract ORU with a new control ID and every observation time moved."""
    payload = copy.deepcopy(example("oru_r01"))
    payload["control_id"] = control_id
    for segment in payload["segments"]:
        if segment["id"] == "OBX":
            segment["fields"][13] = [[[timestamp]]]
    return payload


@pytest.fixture
async def client(database_url: str) -> AsyncIterator[httpx.AsyncClient]:
    await asyncio.to_thread(upgrade, database_url)
    engine = create_engine(database_url)
    sessions = session_factory(engine)
    for payload in (
        example("adt_a01"),
        example("oru_r01"),
        later_oru("SIM000008", "20240315140000+0000"),
    ):
        async with sessions.begin() as session:
            await store_converted(session, convert(payload))
    app = create_app(Settings(public_base_url=BASE, run_consumers=False), sessions=sessions)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as http:
        yield http
    await engine.dispose()


def observation_times(bundle: dict[str, Any]) -> list[str]:
    return [entry["resource"]["effectiveDateTime"] for entry in bundle["entry"]]


async def test_metadata_is_a_capability_statement(client: httpx.AsyncClient) -> None:
    response = await client.get("/fhir/metadata")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/fhir+json"
    statement = CapabilityStatement.model_validate(response.json())
    assert statement.fhirVersion == "4.0.1"


async def test_patient_read_and_not_found(client: httpx.AsyncClient) -> None:
    found = await client.get("/fhir/Patient/MRN0001234")
    assert found.status_code == 200
    assert found.json()["name"][0]["family"] == "Lindgren"
    missing = await client.get("/fhir/Patient/nobody")
    assert missing.status_code == 404
    assert missing.headers["content-type"] == "application/fhir+json"
    outcome = OperationOutcome.model_validate(missing.json())
    assert outcome.issue[0].code == "not-found"


async def test_patient_search_by_identifier(client: httpx.AsyncClient) -> None:
    hit = await client.get(
        "/fhir/Patient", params={"identifier": "https://wardwatch.local/fhir/sid/mrn|MRN0001234"}
    )
    bundle = hit.json()
    Bundle.model_validate(bundle)
    assert bundle["total"] == 1
    assert bundle["entry"][0]["fullUrl"] == f"{BASE}/fhir/Patient/MRN0001234"
    other_system = await client.get("/fhir/Patient", params={"identifier": "urn:other|MRN0001234"})
    assert other_system.json()["total"] == 0


async def test_observation_search_by_subject_and_code(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/fhir/Observation",
        params={"subject": "Patient/MRN0001234", "code": "http://loinc.org|8867-4"},
    )
    bundle = response.json()
    Bundle.model_validate(bundle)
    assert bundle["total"] == 2
    # Newest first by default.
    assert observation_times(bundle) == ["2024-03-15T14:00:00+00:00", "2024-03-15T13:00:00+00:00"]


@pytest.mark.parametrize(
    ("dates", "expected"),
    [
        (["ge2024-03-15T14:00:00Z"], 13),
        (["gt2024-03-15T13:00:00Z"], 13),
        (["lt2024-03-15T14:00:00Z"], 13),
        (["le2024-03-15T13:00:00Z"], 13),
        (["2024-03-15"], 26),
        (["ge2024-03-15T13:00:00Z", "le2024-03-15T13:59:59Z"], 13),
        (["ne2024-03-15T13:00:00Z"], 13),
        (["ge2024-03-16"], 0),
    ],
)
async def test_observation_date_prefixes(
    client: httpx.AsyncClient, dates: list[str], expected: int
) -> None:
    response = await client.get(
        "/fhir/Observation", params=[("subject", "MRN0001234"), *(("date", d) for d in dates)]
    )
    assert response.json()["total"] == expected


async def test_paging_links_and_ascending_sort(client: httpx.AsyncClient) -> None:
    first = (await client.get("/fhir/Observation", params={"_count": "10", "_sort": "date"})).json()
    assert first["total"] == 26
    assert len(first["entry"]) == 10
    links = {link["relation"]: link["url"] for link in first["link"]}
    assert "previous" not in links
    assert links["next"].endswith("_count=10&_sort=date&_offset=10")
    second = (await client.get(links["next"].removeprefix(BASE))).json()
    assert len(second["entry"]) == 10
    assert {link["relation"] for link in second["link"]} == {"self", "next", "previous"}
    third = (
        await client.get(
            "/fhir/Observation", params={"_count": "10", "_sort": "date", "_offset": "20"}
        )
    ).json()
    assert len(third["entry"]) == 6
    assert "next" not in {link["relation"] for link in third["link"]}
    times = observation_times(first) + observation_times(second) + observation_times(third)
    assert times == sorted(times)
    ids = [entry["resource"]["id"] for page in (first, second, third) for entry in page["entry"]]
    assert len(set(ids)) == 26


@pytest.mark.parametrize(
    "params",
    [
        {"date": "yesterday"},
        {"unknown": "1"},
        {"_count": "0"},
        {"_sort": "code"},
        {"subject": "Encounter/E1"},
    ],
)
async def test_bad_search_is_a_400_operation_outcome(
    client: httpx.AsyncClient, params: dict[str, str]
) -> None:
    response = await client.get("/fhir/Observation", params=params)
    assert response.status_code == 400
    assert response.headers["content-type"] == "application/fhir+json"
    OperationOutcome.model_validate(response.json())
