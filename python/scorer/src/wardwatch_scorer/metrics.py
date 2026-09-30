"""Prometheus metrics for the scorer."""

from prometheus_client import Counter, Gauge, Histogram

OBSERVATIONS = Counter(
    "wardwatch_scorer_observations_total", "fhir.observations records by outcome", ["outcome"]
)
HOURS_SCORED = Counter("wardwatch_scorer_hours_scored_total", "ICU hours scored")
ALERTS = Counter("wardwatch_scorer_alerts_total", "Alerts published", ["source"])
ACTIVE_ENCOUNTERS = Gauge("wardwatch_scorer_active_encounters", "Encounters with an open window")
SCORING_SECONDS = Histogram(
    "wardwatch_scorer_hour_seconds",
    "Time to build features and score one hour",
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25),
)
END_TO_END_LATENCY = Histogram(
    "wardwatch_msh7_to_alert_publish_seconds",
    "From MSH-7 of the newest message in the scored hour to the alert being published",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)
