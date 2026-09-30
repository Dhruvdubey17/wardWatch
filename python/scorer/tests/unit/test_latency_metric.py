from datetime import UTC, datetime, timedelta

import pytest
from prometheus_client import REGISTRY
from wardwatch_scorer.service import ScorerService

pytestmark = pytest.mark.unit

NAME = "wardwatch_msh7_to_alert_publish_seconds"


def sample(suffix: str) -> float:
    return REGISTRY.get_sample_value(f"{NAME}_{suffix}") or 0.0


def test_latency_runs_from_msh7_to_now() -> None:
    count, total = sample("count"), sample("sum")
    ScorerService._observe_latency((datetime.now(UTC) - timedelta(seconds=2)).isoformat())
    assert sample("count") == count + 1
    assert 2.0 <= sample("sum") - total < 3.0


def test_a_time_without_offset_is_utc() -> None:
    total = sample("sum")
    naive = (datetime.now(UTC) - timedelta(seconds=5)).replace(tzinfo=None).isoformat()
    ScorerService._observe_latency(naive)
    assert 5.0 <= sample("sum") - total < 6.0


def test_a_clock_ahead_counts_as_zero_and_a_missing_time_is_skipped() -> None:
    count, total = sample("count"), sample("sum")
    ScorerService._observe_latency((datetime.now(UTC) + timedelta(seconds=30)).isoformat())
    assert sample("count") == count + 1
    assert sample("sum") == total
    ScorerService._observe_latency("")
    assert sample("count") == count + 1
