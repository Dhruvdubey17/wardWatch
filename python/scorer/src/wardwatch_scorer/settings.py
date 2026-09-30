"""Scorer configuration, read from WARDWATCH_SCORER_* environment variables."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WARDWATCH_SCORER_", extra="ignore")

    kafka_bootstrap: str = "127.0.0.1:9092"
    consumer_group: str = "wardwatch-scorer"
    observations_topic: str = "fhir.observations"
    alerts_topic: str = "ward.alerts"
    scores_topic: str = "ward.scores"
    # A serving bundle directory from `make train`; without one the scorer runs NEWS2 only.
    bundle_dir: Path | None = None
    fhir_base_url: str = "http://127.0.0.1:8000"
    # An hour is closed and scored once nothing new has arrived for it this long.
    settle_seconds: float = Field(default=1.0, gt=0)
    metrics_port: int = 9465
    metrics_bind: str = "127.0.0.1"
    log_level: str = "INFO"
