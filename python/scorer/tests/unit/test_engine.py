import json
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pytest
from jsonschema import Draft202012Validator
from scorer_support import observation_records, stay_frame
from wardwatch_ml.bundle import ServingBundle
from wardwatch_ml.contracts import contracts_dir
from wardwatch_ml.features import build_features
from wardwatch_ml.policy import alerts_for_stay
from wardwatch_scorer.engine import Outgoing, ScoringEngine, alert_id
from wardwatch_scorer.online import OnlineScorer

pytestmark = pytest.mark.unit


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def engine(bundle: ServingBundle | None, clock: FakeClock) -> ScoringEngine:
    return ScoringEngine(
        OnlineScorer(bundle),
        settle_seconds=1.0,
        wall=lambda: datetime(2026, 1, 1, 12, tzinfo=UTC),
        monotonic=clock,
        record_scores=True,
    )


def replay(
    scoring: ScoringEngine, clock: FakeClock, records: list[dict[str, Any]]
) -> list[Outgoing]:
    out: list[Outgoing] = []
    for record in records:
        clock.now += 0.01
        out += scoring.ingest(record)
    clock.now += 5.0
    out += scoring.tick()
    return out


def validator(topic: str) -> Draft202012Validator:
    schema = json.loads((contracts_dir() / "schemas" / f"{topic}.schema.json").read_text())
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def test_hour_closes_when_the_next_hour_arrives() -> None:
    clock = FakeClock()
    scoring = engine(None, clock)
    records = observation_records(stay_frame("p000004.psv").head(3), "E1")
    hour_one = [r for r in records if r["observation"]["id"].startswith("E1-1-")]
    hour_two = [r for r in records if r["observation"]["id"].startswith("E1-2-")]
    assert all(scoring.ingest(r) == [] for r in hour_one)
    out = scoring.ingest(hour_two[0])
    assert [o.payload["icu_hour"] for o in out if o.kind == "score"] == [1]


def test_quiet_hour_is_closed_after_settling_only() -> None:
    clock = FakeClock()
    scoring = engine(None, clock)
    for record in observation_records(stay_frame("p000004.psv").head(1), "E1"):
        scoring.ingest(record)
    clock.now += 0.5
    assert scoring.tick() == []
    clock.now += 0.6
    assert [o.payload["icu_hour"] for o in scoring.tick() if o.kind == "score"] == [1]
    assert scoring.tick() == []


def test_skipped_hours_are_scored_as_empty_rows() -> None:
    clock = FakeClock()
    scoring = engine(None, clock)
    records = observation_records(stay_frame("p000004.psv").head(6), "E1")
    keep = [r for r in records if r["observation"]["id"].split("-")[1] in {"1", "5"}]
    out = replay(scoring, clock, keep)
    assert [o.payload["icu_hour"] for o in out if o.kind == "score"] == [1, 2, 3, 4, 5]


def test_late_result_is_kept_but_not_rescored() -> None:
    clock = FakeClock()
    scoring = engine(None, clock)
    records = observation_records(stay_frame("p000004.psv").head(4), "E1")
    hour_two = [r for r in records if r["observation"]["id"].startswith("E1-2-")]
    others = [r for r in records if r not in hour_two]
    first = replay(scoring, clock, others)
    assert [o.payload["icu_hour"] for o in first if o.kind == "score"] == [1, 2, 3, 4]
    late = replay(scoring, clock, hour_two)
    assert [o for o in late if o.kind == "score"] == []
    assert scoring.windows["E1"].values[2]


def test_duplicate_records_are_ignored() -> None:
    clock = FakeClock()
    scoring = engine(None, clock)
    records = observation_records(stay_frame("p000004.psv").head(3), "E1")
    once = replay(scoring, clock, records)
    second_clock = FakeClock()
    again = replay(engine(None, second_clock), second_clock, records + records)
    assert len([o for o in once if o.kind == "score"]) == len(
        [o for o in again if o.kind == "score"]
    )


def test_record_without_admission_is_dropped() -> None:
    record = observation_records(stay_frame("p000004.psv").head(1), "E1")[0]
    record["encounter_start"] = None
    scoring = engine(None, FakeClock())
    assert scoring.ingest(record) == []
    assert scoring.windows == {}


def test_news2_only_mode_without_a_bundle() -> None:
    clock = FakeClock()
    scoring = engine(None, clock)
    out = replay(scoring, clock, observation_records(stay_frame("p000001.psv"), "E1"))
    scores = [o.payload for o in out if o.kind == "score"]
    alerts = [o.payload for o in out if o.kind == "alert"]
    assert all(s["raw_score"] is None and s["model_version"] == "news2-only" for s in scores)
    assert alerts
    assert {a["source"] for a in alerts} == {"news2"}
    for alert in alerts:
        assert alert["news2"]["total"] >= 5
        assert alert["top_factors"] == []
        assert alert["calibrated_probability"] is None


def test_payloads_match_their_contracts(bundle: ServingBundle) -> None:
    clock = FakeClock()
    scoring = engine(bundle, clock)
    out = replay(scoring, clock, observation_records(stay_frame("p000001.psv"), "E1"))
    alerts = validator("ward.alerts")
    scores = validator("ward.scores")
    kinds = {o.kind for o in out}
    assert kinds == {"alert", "score"}
    for item in out:
        schema = alerts if item.kind == "alert" else scores
        assert list(schema.iter_errors(item.payload)) == [], item.payload
        assert item.key == "MRN-E1"
    model_alerts = [o.payload for o in out if o.kind == "alert" and o.payload["source"] == "model"]
    assert model_alerts
    assert all(1 <= len(a["top_factors"]) <= 5 for a in model_alerts)
    alerted_hours = {o.payload["icu_hour"] for o in out if o.kind == "alert"}
    assert {
        s.payload["icu_hour"] for s in out if s.kind == "score" and s.payload["alerted"]
    } == alerted_hours


def test_runtime_policy_matches_offline_policy(bundle: ServingBundle) -> None:
    clock = FakeClock()
    scoring = engine(bundle, clock)
    frame = stay_frame("p000009.psv")
    out = replay(scoring, clock, observation_records(frame, "E9"))
    online_model = sorted(
        o.payload["icu_hour"] for o in out if o.kind == "alert" and o.payload["source"] == "model"
    )
    online_news2 = sorted(
        o.payload["icu_hour"] for o in out if o.kind == "alert" and o.payload["source"] == "news2"
    )
    scored_hours = [score.icu_hour for _, score in scoring.scored]
    features = build_features(frame, bundle.spec).set_index("hour").loc[scored_hours].reset_index()
    margins = bundle.margin(features)
    offline_model = [
        h
        for h, fired in zip(
            scored_hours, alerts_for_stay(bundle.policy, scored_hours, margins), strict=True
        )
        if fired
    ]
    offline_news2 = [
        h
        for h, fired in zip(
            scored_hours,
            alerts_for_stay(bundle.news2_policy, scored_hours, features["news2_total"]),
            strict=True,
        )
        if fired
    ]
    assert online_model == offline_model
    assert online_news2 == offline_news2


def test_alert_ids_are_stable() -> None:
    assert alert_id("E1", 5, "model") == alert_id("E1", 5, "model")
    assert alert_id("E1", 5, "model") != alert_id("E1", 5, "news2")
    assert len({alert_id("E1", hour, "model") for hour in range(50)}) == 50
    assert np.isfinite(len(alert_id("E1", 1, "x")))
