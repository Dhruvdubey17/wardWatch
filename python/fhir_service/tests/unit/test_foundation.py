import json
import logging
import sys

import pytest
from wardwatch_fhir.log_config import JsonFormatter
from wardwatch_fhir.models import Base
from wardwatch_fhir.settings import Settings

pytestmark = pytest.mark.unit


def test_settings_read_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WARDWATCH_FHIR_KAFKA_BOOTSTRAP", "kafka:9092")
    monkeypatch.setenv("WARDWATCH_FHIR_AUTO_ESCALATE_AFTER_MINUTES", "5")
    settings = Settings()
    assert settings.kafka_bootstrap == "kafka:9092"
    assert settings.auto_escalate_after_minutes == 5.0
    assert settings.alerts_topic == "ward.alerts"


def test_settings_reject_non_positive_escalation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WARDWATCH_FHIR_AUTO_ESCALATE_AFTER_MINUTES", "0")
    with pytest.raises(ValueError, match="greater than 0"):
        Settings()


def test_json_formatter_keeps_extra_fields() -> None:
    record = logging.LogRecord("wardwatch", logging.INFO, __file__, 1, "stored %s", ("x",), None)
    record.mrn = "MRN1"
    entry = json.loads(JsonFormatter().format(record))
    assert entry["message"] == "stored x"
    assert entry["level"] == "info"
    assert entry["mrn"] == "MRN1"
    assert entry["time"].endswith("+00:00")


def fail() -> None:
    raise ValueError("boom")


def test_json_formatter_includes_exceptions() -> None:
    try:
        fail()
    except ValueError:
        record = logging.LogRecord("w", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    entry = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in entry["exception"]


def test_tables_in_metadata() -> None:
    assert set(Base.metadata.tables) == {
        "patients",
        "encounters",
        "observations",
        "alerts",
        "alert_events",
    }
    observation = Base.metadata.tables["observations"]
    assert {"patient_id", "encounter_id", "code", "effective", "value", "resource"} <= set(
        observation.columns.keys()
    )
