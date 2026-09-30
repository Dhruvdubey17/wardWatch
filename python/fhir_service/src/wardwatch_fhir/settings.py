"""Service configuration, read from WARDWATCH_FHIR_* environment variables."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WARDWATCH_FHIR_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://wardwatch:wardwatch_dev@127.0.0.1:5432/wardwatch"
    kafka_bootstrap: str = "127.0.0.1:9092"
    consumer_group: str = "wardwatch-fhir"
    validated_topic: str = "hl7.validated"
    deadletter_topic: str = "hl7.deadletter"
    observations_topic: str = "fhir.observations"
    alerts_topic: str = "ward.alerts"
    alert_events_topic: str = "ward.alert-events"
    scores_topic: str = "ward.scores"
    # The base of absolute URLs in FHIR Bundles (Bundle.link, fullUrl).
    public_base_url: str = "http://127.0.0.1:8000"
    # Alerts left open this long are escalated by the service itself.
    auto_escalate_after_minutes: float = Field(default=15.0, gt=0)
    auto_escalate_interval_seconds: float = Field(default=30.0, gt=0)
    # Comment lines on the event stream keep proxies from closing idle connections.
    sse_heartbeat_seconds: float = Field(default=15.0, gt=0)
    run_consumers: bool = True
    log_level: str = "INFO"
