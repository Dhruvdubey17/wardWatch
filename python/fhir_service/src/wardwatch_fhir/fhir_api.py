"""The FHIR-style read API: metadata, Patient and Observation search.

Responses use application/fhir+json. Search results are searchset Bundles
with self, next and previous links; any error is an OperationOutcome.
"""

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wardwatch_fhir.converter import LOINC_SYSTEM, MRN_SYSTEM
from wardwatch_fhir.fhir_query import (
    DateFilter,
    SearchParameterError,
    parse_count,
    parse_date,
    parse_offset,
    parse_reference,
    parse_sort,
    parse_token,
)
from wardwatch_fhir.models import Observation, Patient

FHIR_JSON = "application/fhir+json"
FHIR_VERSION = "4.0.1"
OBSERVATION_PARAMETERS = {"subject", "patient", "code", "date", "_count", "_sort", "_offset"}
PATIENT_PARAMETERS = {"identifier", "_count", "_offset"}

router = APIRouter(prefix="/fhir")


def fhir_response(content: dict[str, Any], status_code: int = 200) -> JSONResponse:
    return JSONResponse(content, status_code=status_code, media_type=FHIR_JSON)


def operation_outcome(status_code: int, code: str, diagnostics: str) -> JSONResponse:
    return fhir_response(
        {
            "resourceType": "OperationOutcome",
            "issue": [{"severity": "error", "code": code, "diagnostics": diagnostics}],
        },
        status_code,
    )


def _check_parameters(request: Request, allowed: set[str]) -> None:
    unknown = sorted(set(request.query_params) - allowed)
    if unknown:
        raise SearchParameterError(f"unsupported search parameters: {', '.join(unknown)}")


def _session(request: Request) -> AsyncSession:
    session: AsyncSession = request.app.state.sessions()
    return session


def _page_links(request: Request, total: int, offset: int, count: int) -> list[dict[str, str]]:
    base = str(request.app.state.settings.public_base_url).rstrip("/") + request.url.path

    def url(new_offset: int) -> str:
        items = [
            (key, value) for key, value in request.query_params.multi_items() if key != "_offset"
        ]
        items.append(("_offset", str(new_offset)))
        return f"{base}?{urlencode(items)}"

    links = [{"relation": "self", "url": url(offset)}]
    if offset + count < total:
        links.append({"relation": "next", "url": url(offset + count)})
    if offset > 0:
        links.append({"relation": "previous", "url": url(max(0, offset - count))})
    return links


def _bundle(
    request: Request,
    resource_type: str,
    resources: list[dict[str, Any]],
    *,
    total: int,
    offset: int,
    count: int,
) -> dict[str, Any]:
    base = str(request.app.state.settings.public_base_url).rstrip("/")
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": total,
        "link": _page_links(request, total, offset, count),
        "entry": [
            {
                "fullUrl": f"{base}/fhir/{resource_type}/{resource['id']}",
                "resource": resource,
                "search": {"mode": "match"},
            }
            for resource in resources
        ],
    }


def _date_condition(date: DateFilter) -> Any:
    column = Observation.effective
    conditions = {
        "eq": (column >= date.start) & (column < date.end),
        "ne": (column < date.start) | (column >= date.end),
        "lt": column < date.start,
        "le": column < date.end,
        "gt": column >= date.end,
        "ge": column >= date.start,
    }
    return conditions[date.prefix]


@router.get("/metadata")
async def metadata(request: Request) -> JSONResponse:
    """A minimal CapabilityStatement describing what this server supports."""
    base = str(request.app.state.settings.public_base_url).rstrip("/")
    return fhir_response(
        {
            "resourceType": "CapabilityStatement",
            "status": "active",
            "date": "2026-09-30",
            "kind": "instance",
            "software": {"name": "WardWatch FHIR service"},
            "implementation": {"description": "WardWatch research demo", "url": f"{base}/fhir"},
            "fhirVersion": FHIR_VERSION,
            "format": ["application/fhir+json"],
            "rest": [
                {
                    "mode": "server",
                    "resource": [
                        {
                            "type": "Patient",
                            "interaction": [{"code": "read"}, {"code": "search-type"}],
                            "searchParam": [{"name": "identifier", "type": "token"}],
                        },
                        {
                            "type": "Observation",
                            "interaction": [{"code": "search-type"}],
                            "searchParam": [
                                {"name": "subject", "type": "reference"},
                                {"name": "patient", "type": "reference"},
                                {"name": "code", "type": "token"},
                                {"name": "date", "type": "date"},
                            ],
                        },
                    ],
                }
            ],
        }
    )


@router.get("/Patient/{patient_id}")
async def read_patient(patient_id: str, request: Request) -> JSONResponse:
    async with _session(request) as session:
        patient = await session.get(Patient, patient_id)
    if patient is None:
        return operation_outcome(404, "not-found", f"Patient/{patient_id} is not known")
    return fhir_response(patient.resource)


@router.get("/Patient")
async def search_patients(request: Request) -> JSONResponse:
    _check_parameters(request, PATIENT_PARAMETERS)
    count = parse_count(request.query_params.get("_count"))
    offset = parse_offset(request.query_params.get("_offset"))
    query = select(Patient)
    identifier = request.query_params.get("identifier")
    if identifier is not None:
        token = parse_token(identifier)
        if token.system not in (None, MRN_SYSTEM):
            query = query.where(false())
        else:
            query = query.where(Patient.mrn == token.code)
    async with _session(request) as session:
        total = (
            await session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        rows = (
            (await session.execute(query.order_by(Patient.id).offset(offset).limit(count)))
            .scalars()
            .all()
        )
    return fhir_response(
        _bundle(
            request,
            "Patient",
            [row.resource for row in rows],
            total=total,
            offset=offset,
            count=count,
        )
    )


@router.get("/Observation")
async def search_observations(request: Request) -> JSONResponse:
    _check_parameters(request, OBSERVATION_PARAMETERS)
    parameters = request.query_params
    count = parse_count(parameters.get("_count"))
    offset = parse_offset(parameters.get("_offset"))
    sort = parse_sort(parameters.get("_sort"))
    query = select(Observation)
    subject = parameters.get("subject") or parameters.get("patient")
    if subject is not None:
        query = query.where(Observation.patient_id == parse_reference(subject, "Patient"))
    code = parameters.get("code")
    if code is not None:
        token = parse_token(code)
        if token.system not in (None, LOINC_SYSTEM):
            query = query.where(false())
        else:
            query = query.where(Observation.code == token.code)
    for value in parameters.getlist("date"):
        query = query.where(_date_condition(parse_date(value)))
    order = Observation.effective.desc() if sort == "-date" else Observation.effective.asc()
    async with _session(request) as session:
        total = (
            await session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        rows = (
            (
                await session.execute(
                    query.order_by(order, Observation.id).offset(offset).limit(count)
                )
            )
            .scalars()
            .all()
        )
    return fhir_response(
        _bundle(
            request,
            "Observation",
            [row.resource for row in rows],
            total=total,
            offset=offset,
            count=count,
        )
    )
