"""Consumes ward.alerts and stores each alert as open; offsets commit after the write."""

import json
import logging
from datetime import UTC, datetime

from aiokafka import AIOKafkaConsumer

from wardwatch_fhir.alert_service import AlertService
from wardwatch_fhir.metrics import ALERTS_RECEIVED, END_TO_END_LATENCY

log = logging.getLogger(__name__)


class AlertsConsumer:
    def __init__(self, consumer: AIOKafkaConsumer, alerts: AlertService) -> None:
        self._consumer = consumer
        self._alerts = alerts

    async def run_once(self, timeout_ms: int = 1000) -> int:
        batches = await self._consumer.getmany(timeout_ms=timeout_ms)
        handled = 0
        for partition, records in batches.items():
            for record in records:
                payload = json.loads(record.value or b"{}")
                created = await self._alerts.store(payload)
                if created:
                    ALERTS_RECEIVED.labels(source=payload["source"]).inc()
                    self._observe_latency(str(payload.get("message_time", "")))
                await self._consumer.commit({partition: record.offset + 1})
                handled += 1
        return handled

    @staticmethod
    def _observe_latency(message_time: str) -> None:
        try:
            sent = datetime.fromisoformat(message_time)
        except ValueError:
            log.warning(
                "alert has an unreadable message_time", extra={"message_time": message_time}
            )
            return
        if sent.tzinfo is None:
            sent = sent.replace(tzinfo=UTC)
        END_TO_END_LATENCY.observe(max(0.0, (datetime.now(UTC) - sent).total_seconds()))
