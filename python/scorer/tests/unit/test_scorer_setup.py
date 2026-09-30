from pathlib import Path

import pytest
from prometheus_client import generate_latest
from wardwatch_scorer.main import load_bundle
from wardwatch_scorer.settings import Settings

pytestmark = pytest.mark.unit


def test_missing_bundle_means_news2_only(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert load_bundle(Settings(bundle_dir=tmp_path)) is None
    assert load_bundle(Settings(bundle_dir=None)) is None
    assert "scoring NEWS2 only" in caplog.text


def test_bundle_directory_is_loaded(bundle_directory: Path) -> None:
    loaded = load_bundle(Settings(bundle_dir=bundle_directory))
    assert loaded is not None
    assert loaded.model_version == "xgb-test-0000000"


def test_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WARDWATCH_SCORER_SETTLE_SECONDS", "0.5")
    monkeypatch.setenv("WARDWATCH_SCORER_BUNDLE_DIR", "/opt/bundle")
    settings = Settings()
    assert settings.settle_seconds == 0.5
    assert settings.bundle_dir == Path("/opt/bundle")


def test_metrics_are_registered() -> None:
    text = generate_latest().decode()
    for name in (
        "wardwatch_scorer_hours_scored_total",
        "wardwatch_scorer_alerts_total",
        "wardwatch_msh7_to_alert_publish_seconds_bucket",
        "wardwatch_scorer_active_encounters",
    ):
        assert name in text
