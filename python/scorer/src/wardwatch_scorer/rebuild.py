"""Restores scorer state from the FHIR service after a restart.

For each in-progress encounter it reloads enough recent Observations to
reproduce the next hour's features exactly (FeatureSpec.history_hours), the
last scored hour, and each alert policy's last alert, so the alerts that
follow a restart are the ones an uninterrupted scorer would raise.
"""

import logging
from datetime import datetime, timedelta
from typing import Any

import httpx

from wardwatch_scorer.engine import ScoringEngine, Source
from wardwatch_scorer.window import EncounterWindow

log = logging.getLogger(__name__)
PAGE_SIZE = 1000


async def _observations(
    client: httpx.AsyncClient, patient_id: str, encounter_id: str, since: datetime
) -> list[dict[str, Any]]:
    params: dict[str, str] | None = {
        "subject": f"Patient/{patient_id}",
        "date": f"ge{since.isoformat()}",
        "_sort": "date",
        "_count": str(PAGE_SIZE),
    }
    url = "/fhir/Observation"
    found: list[dict[str, Any]] = []
    while url:
        response = await client.get(url, params=params)
        response.raise_for_status()
        bundle = response.json()
        for entry in bundle.get("entry", []):
            resource = entry["resource"]
            if resource["encounter"]["reference"] == f"Encounter/{encounter_id}":
                found.append(resource)
        following = next(
            (link["url"] for link in bundle["link"] if link["relation"] == "next"), None
        )
        # The next link is absolute and already carries every parameter.
        url, params = (httpx.URL(following).raw_path.decode(), None) if following else ("", None)
    return found


async def rebuild(engine: ScoringEngine, client: httpx.AsyncClient) -> int:
    """Load every in-progress encounter into the engine; returns how many."""
    census = (await client.get("/api/ward/census")).raise_for_status().json()
    alerts = (await client.get("/api/alerts")).raise_for_status().json()
    history = engine.scorer.spec.history_hours
    now = engine.monotonic()
    restored = 0
    for bed in census:
        if not bed["admitted_at"]:
            continue
        window = EncounterWindow(
            mrn=bed["mrn"],
            encounter_id=bed["encounter_id"],
            admitted_at=datetime.fromisoformat(bed["admitted_at"]),
        )
        last_scored = bed["latest_score"]["icu_hour"] if bed["latest_score"] else 0
        since = window.admitted_at + timedelta(hours=max(0, last_scored - history))
        for resource in await _observations(client, bed["patient_id"], bed["encounter_id"], since):
            window.add(resource, "", now)
        window.last_scored_hour = last_scored
        engine.windows[window.encounter_id] = window
        sources: tuple[Source, ...] = ("model", "news2")
        for source in sources:
            if source == "model" and engine.scorer.bundle is None:
                continue
            raised = [
                a
                for a in alerts
                if a["encounter_id"] == window.encounter_id and a["source"] == source
            ]
            if raised:
                latest = max(raised, key=lambda alert: alert["icu_hour"])
                state = engine.policy_state(window.encounter_id, source)
                state.last_alert_hour = float(latest["icu_hour"])
                state.last_alert_score = float(latest["raw_score"])
        restored += 1
    log.info("rebuilt scorer windows", extra={"encounters": restored})
    return restored
