import json

import pytest
from wardwatch_fhir.main import openapi_document
from wardwatch_ml.contracts import contracts_dir

pytestmark = pytest.mark.unit


def test_committed_openapi_matches_the_app() -> None:
    committed = json.loads((contracts_dir() / "openapi.json").read_text())
    assert committed == json.loads(json.dumps(openapi_document())), (
        "run `make openapi` and commit the result"
    )


def test_ward_endpoints_declare_their_responses() -> None:
    paths = openapi_document()["paths"]
    assert isinstance(paths, dict)
    census = paths["/api/ward/census"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert census["items"]["$ref"].endswith("/CensusBed")
    acknowledge = paths["/api/alerts/{alert_id}/acknowledge"]["post"]["responses"]
    assert {"200", "409", "412", "428"} <= set(acknowledge)
