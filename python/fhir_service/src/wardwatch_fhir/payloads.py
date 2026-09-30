"""Kafka payloads the FHIR service produces, matching contracts/schemas."""

import base64
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = 1


def received_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def observation_payload(
    mrn: str, encounter_id: str, message_time: str, observation: dict[str, Any]
) -> dict[str, Any]:
    """fhir.observations (contracts/schemas/fhir.observations.schema.json)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "mrn": mrn,
        "encounter_id": encounter_id,
        "message_time": message_time,
        "observation": observation,
    }


def deadletter_payload(
    error_code: str, error_detail: str, control_id: str | None, raw: bytes
) -> dict[str, Any]:
    """hl7.deadletter with stage=fhir (contracts/schemas/hl7.deadletter.schema.json)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "received_at": received_now(),
        "stage": "fhir",
        "error_code": error_code,
        "error_detail": error_detail,
        "control_id": control_id,
        "raw_base64": base64.b64encode(raw).decode("ascii"),
        "raw_truncated": False,
    }
