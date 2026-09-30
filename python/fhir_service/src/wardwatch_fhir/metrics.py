"""Prometheus metrics for the FHIR service."""

from prometheus_client import Counter, Gauge, Histogram

CONVERSIONS = Counter(
    "wardwatch_fhir_messages_converted_total",
    "hl7.validated messages stored as FHIR",
    ["message_type"],
)
OBSERVATIONS_STORED = Counter("wardwatch_fhir_observations_stored_total", "Observations upserted")
DEADLETTERED = Counter(
    "wardwatch_fhir_deadlettered_total",
    "Messages sent to hl7.deadletter by the FHIR service",
    ["error_code"],
)
ALERTS_RECEIVED = Counter(
    "wardwatch_fhir_alerts_received_total", "ward.alerts records stored", ["source"]
)
ALERT_TRANSITIONS = Counter(
    "wardwatch_fhir_alert_transitions_total",
    "Alert workflow transitions",
    ["transition", "actor_kind"],
)
OPEN_ALERTS = Gauge("wardwatch_fhir_open_alerts", "Alerts in state open")
TIME_TO_ACKNOWLEDGE = Histogram(
    "wardwatch_fhir_time_to_acknowledge_seconds",
    "Wall time from an alert being stored to its acknowledgement",
    buckets=(30, 60, 120, 300, 600, 900, 1800, 3600, 7200),
)
END_TO_END_LATENCY = Histogram(
    "wardwatch_fhir_msh7_to_alert_stored_seconds",
    "From the MSH-7 of the scored hour's message to the alert being stored",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)
ALERT_EVENT_PUBLISH_FAILURES = Counter(
    "wardwatch_fhir_alert_event_publish_failures_total",
    "Committed transitions whose ward.alert-events record could not be published",
)
