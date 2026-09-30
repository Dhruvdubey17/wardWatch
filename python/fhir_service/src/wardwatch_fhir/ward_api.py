"""The ward API used by the dashboard: alerts and their workflow.

Writes need If-Match with the alert's ETag. A stale ETag gets 412 and a
transition the workflow does not allow gets 409; both bodies carry the alert
as it now is, including who changed it last.
"""

from typing import Annotated, Any, Literal, get_args

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from wardwatch_fhir.alert_service import (
    AlertNotFoundError,
    AlertService,
    ConflictError,
    PreconditionFailedError,
)
from wardwatch_fhir.alert_states import (
    CLINICIAN_ESCALATION_REASONS,
    STATUSES,
    Transition,
    parse_etag,
)

router = APIRouter(prefix="/api")

EscalationReason = Literal[
    "clinical_deterioration",
    "senior_review_needed",
    "sepsis_pathway_started",
    "critical_care_outreach",
    "other",
]
assert set(get_args(EscalationReason)) == set(CLINICIAN_ESCALATION_REASONS)


class ActionBody(BaseModel):
    # Authentication is out of scope; the clinician names themselves.
    actor: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=2000)


class EscalateBody(ActionBody):
    reason: EscalationReason


def _service(request: Request) -> AlertService:
    service: AlertService = request.app.state.alerts
    return service


def _error(status_code: int, detail: str, alert: dict[str, Any] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"detail": detail}
    headers = {}
    if alert is not None:
        body["alert"] = alert
        headers["ETag"] = alert["etag"]
    return JSONResponse(body, status_code=status_code, headers=headers)


@router.get("/alerts")
async def list_alerts(
    request: Request,
    status: Annotated[str | None, Query(description="comma-separated statuses")] = None,
) -> Any:
    statuses = [part for part in (status or "").split(",") if part]
    unknown = [part for part in statuses if part not in STATUSES]
    if unknown:
        return _error(400, f"unknown status: {', '.join(unknown)}")
    return await _service(request).list_alerts(statuses or None)


@router.get("/alerts/{alert_id}")
async def get_alert(alert_id: str, request: Request, response: Response) -> Any:
    try:
        alert = await _service(request).get(alert_id)
    except AlertNotFoundError:
        return _error(404, f"alert {alert_id} is not known")
    response.headers["ETag"] = alert["etag"]
    return alert


async def _act(
    request: Request,
    alert_id: str,
    transition: Transition,
    body: ActionBody,
    *,
    if_match: str | None,
    reason: str | None = None,
) -> Any:
    if if_match is None:
        return _error(428, "If-Match with the alert's ETag is required")
    version = parse_etag(if_match)
    if version is None:
        return _error(400, f"If-Match '{if_match}' is not an ETag")
    try:
        alert = await _service(request).transition(
            alert_id,
            transition,
            expected_version=version,
            actor=body.actor,
            reason=reason,
            note=body.note,
        )
    except AlertNotFoundError:
        return _error(404, f"alert {alert_id} is not known")
    except PreconditionFailedError as error:
        who = error.current["updated_by"] or "someone"
        return _error(
            412,
            f"the alert changed since you loaded it; {who} set it to {error.current['status']}",
            error.current,
        )
    except ConflictError as error:
        return _error(409, error.detail, error.current)
    return JSONResponse(alert, headers={"ETag": alert["etag"]})


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge(
    alert_id: str,
    body: ActionBody,
    request: Request,
    if_match: Annotated[str | None, Header()] = None,
) -> Any:
    return await _act(request, alert_id, "acknowledge", body, if_match=if_match)


@router.post("/alerts/{alert_id}/escalate")
async def escalate(
    alert_id: str,
    body: EscalateBody,
    request: Request,
    if_match: Annotated[str | None, Header()] = None,
) -> Any:
    return await _act(request, alert_id, "escalate", body, if_match=if_match, reason=body.reason)


@router.post("/alerts/{alert_id}/resolve")
async def resolve(
    alert_id: str,
    body: ActionBody,
    request: Request,
    if_match: Annotated[str | None, Header()] = None,
) -> Any:
    return await _act(request, alert_id, "resolve", body, if_match=if_match)
