"""Turns a stream of Observations into hourly scores and alerts.

The engine is synchronous and takes its clocks as arguments, so tests drive it
with fake time. The Kafka service around it only moves records in and out.
"""

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from wardwatch_ml.policy import AlertPolicy, AlertPolicyState
from wardwatch_ml.thresholds import news2_policy

from wardwatch_scorer.metrics import ALERTS, HOURS_SCORED, OBSERVATIONS, SCORING_SECONDS
from wardwatch_scorer.online import NEWS2_VERSION, HourScore, OnlineScorer
from wardwatch_scorer.window import Added, EncounterWindow

log = logging.getLogger(__name__)

# Alert IDs are derived from encounter, hour and source, so scoring the same
# hour twice (after a restart, say) cannot raise two alerts.
ALERT_NAMESPACE = uuid.UUID("7d5c6f2e-3a7b-4b8e-9f0d-2c1e6a5b4d30")
Source = Literal["model", "news2"]


@dataclass(frozen=True)
class Outgoing:
    kind: Literal["alert", "score"]
    key: str
    payload: dict[str, Any]


def alert_id(encounter_id: str, hour: int, source: str) -> str:
    return str(uuid.uuid5(ALERT_NAMESPACE, f"{encounter_id}/{hour}/{source}"))


def _now_iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class ScoringEngine:
    scorer: OnlineScorer
    settle_seconds: float
    wall: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    monotonic: Callable[[], float] = time.monotonic
    windows: dict[str, EncounterWindow] = field(default_factory=dict)
    policies: dict[tuple[str, Source], AlertPolicyState] = field(default_factory=dict)
    # Every HourScore produced, kept only when a test asks for it.
    record_scores: bool = False
    scored: list[tuple[str, HourScore]] = field(default_factory=list)

    def _policy(self, source: Source) -> AlertPolicy:
        if source == "model":
            assert self.scorer.bundle is not None
            return self.scorer.bundle.policy
        return self.scorer.bundle.news2_policy if self.scorer.bundle is not None else news2_policy()

    def policy_state(self, encounter_id: str, source: Source) -> AlertPolicyState:
        key = (encounter_id, source)
        if key not in self.policies:
            self.policies[key] = AlertPolicyState(self._policy(source))
        return self.policies[key]

    def ingest(self, record: dict[str, Any]) -> list[Outgoing]:
        """Take one fhir.observations record; returns anything that became ready."""
        encounter_id = str(record["encounter_id"])
        window = self.windows.get(encounter_id)
        if window is None:
            if not record.get("encounter_start"):
                OBSERVATIONS.labels(outcome="no_admission").inc()
                return []
            window = EncounterWindow(
                mrn=str(record["mrn"]),
                encounter_id=encounter_id,
                admitted_at=datetime.fromisoformat(record["encounter_start"]),
            )
            self.windows[encounter_id] = window
        added, hour = window.add(
            record["observation"], str(record.get("message_time", "")), self.monotonic()
        )
        OBSERVATIONS.labels(outcome=added.value).inc()
        if added is not Added.ACCEPTED:
            return []
        # A result for a later hour means every earlier hour is complete.
        return self._close(window, hour - 1)

    def tick(self) -> list[Outgoing]:
        """Close hours that have been quiet for settle_seconds."""
        now = self.monotonic()
        ready: list[Outgoing] = []
        for window in self.windows.values():
            if (
                window.newest_hour > window.last_scored_hour
                and now - window.last_arrival >= self.settle_seconds
            ):
                ready += self._close(window, window.newest_hour)
        return ready

    def _close(self, window: EncounterWindow, up_to: int) -> list[Outgoing]:
        ready: list[Outgoing] = []
        for hour in range(window.last_scored_hour + 1, up_to + 1):
            started = time.perf_counter()
            score = self.scorer.score(window, hour)
            SCORING_SECONDS.observe(time.perf_counter() - started)
            HOURS_SCORED.inc()
            window.last_scored_hour = hour
            if self.record_scores:
                self.scored.append((window.encounter_id, score))
            alerts = self._alerts(window, score)
            ready += alerts
            ready.append(
                Outgoing("score", window.mrn, self._score_payload(window, score, bool(alerts)))
            )
        return ready

    def _alerts(self, window: EncounterWindow, score: HourScore) -> list[Outgoing]:
        fired: list[Outgoing] = []
        checks: list[tuple[Source, float | None]] = [("news2", float(score.news2_total))]
        if self.scorer.bundle is not None:
            checks.insert(0, ("model", score.raw_score))
        for source, value in checks:
            if value is None or not self.policy_state(window.encounter_id, source).step(
                score.icu_hour, value
            ):
                continue
            ALERTS.labels(source=source).inc()
            fired.append(Outgoing("alert", window.mrn, self._alert_payload(window, score, source)))
        return fired

    def _alert_payload(
        self, window: EncounterWindow, score: HourScore, source: Source
    ) -> dict[str, Any]:
        is_model = source == "model"
        return {
            "schema_version": 1,
            "alert_id": alert_id(window.encounter_id, score.icu_hour, source),
            "mrn": window.mrn,
            "encounter_id": window.encounter_id,
            "source": source,
            "raised_at": _now_iso(self.wall()),
            "icu_hour": score.icu_hour,
            "raw_score": score.raw_score if is_model else float(score.news2_total),
            "calibrated_probability": score.calibrated_probability if is_model else None,
            "news2": score.news2_payload(),
            "top_factors": [factor.to_dict() for factor in self.scorer.explain(score)]
            if is_model
            else [],
            "model_version": self.scorer.model_version if is_model else NEWS2_VERSION,
            "message_time": self._message_time(window, score.icu_hour),
        }

    def _score_payload(
        self, window: EncounterWindow, score: HourScore, alerted: bool
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "mrn": window.mrn,
            "encounter_id": window.encounter_id,
            "icu_hour": score.icu_hour,
            "hour_ending": window.hour_ending(score.icu_hour).isoformat(),
            "scored_at": _now_iso(self.wall()),
            "news2": score.news2_payload(),
            "raw_score": score.raw_score,
            "calibrated_probability": score.calibrated_probability,
            "model_version": self.scorer.model_version,
            "alerted": alerted,
        }

    @staticmethod
    def _message_time(window: EncounterWindow, hour: int) -> str:
        # An hour with no results of its own carries the latest earlier MSH-7.
        known = [time for known_hour, time in window.message_times.items() if known_hour <= hour]
        return max(known, default="")
