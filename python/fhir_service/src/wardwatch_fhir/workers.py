"""Background tasks the service runs: Kafka consumers and the escalation loop."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from wardwatch_fhir.alert_service import AlertService
from wardwatch_fhir.alerts_consumer import AlertsConsumer
from wardwatch_fhir.converter import Converted
from wardwatch_fhir.escalation import run_escalation
from wardwatch_fhir.events import EventBus, vitals_event
from wardwatch_fhir.scores_consumer import ScoresConsumer
from wardwatch_fhir.settings import Settings
from wardwatch_fhir.validated_consumer import ValidatedConsumer

log = logging.getLogger(__name__)


async def run_until_stopped(
    name: str, step: Callable[[], Awaitable[int]], stop: asyncio.Event
) -> None:
    while not stop.is_set():
        await step()
    log.info("worker stopped", extra={"worker": name})


def kafka_consumer(settings: Settings, topic: str, group_suffix: str) -> AIOKafkaConsumer:
    return AIOKafkaConsumer(
        topic,
        bootstrap_servers=settings.kafka_bootstrap,
        group_id=f"{settings.consumer_group}.{group_suffix}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )


class Workers:
    """Starts and stops everything that runs beside the HTTP server."""

    def __init__(
        self,
        settings: Settings,
        sessions: async_sessionmaker[AsyncSession],
        bus: EventBus,
        producer: AIOKafkaProducer,
        alerts: AlertService,
    ) -> None:
        self._settings = settings
        self._sessions = sessions
        self._bus = bus
        self._producer = producer
        self._alerts = alerts
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._consumers: list[AIOKafkaConsumer] = []

    async def start(self) -> None:
        settings = self._settings
        validated = kafka_consumer(settings, settings.validated_topic, "validated")
        alerts = kafka_consumer(settings, settings.alerts_topic, "alerts")
        scores = kafka_consumer(settings, settings.scores_topic, "scores")
        self._consumers = [validated, alerts, scores]
        for consumer in self._consumers:
            await consumer.start()

        async def on_stored(converted: Converted) -> None:
            event = vitals_event(converted)
            if event is not None:
                self._bus.publish(event)

        steps = {
            "validated": ValidatedConsumer(
                validated, self._producer, self._sessions, settings, on_stored=on_stored
            ).run_once,
            "alerts": AlertsConsumer(alerts, self._alerts).run_once,
            "scores": ScoresConsumer(scores, self._sessions, self._bus).run_once,
        }
        for name, step in steps.items():
            self._tasks.append(
                asyncio.create_task(run_until_stopped(name, step, self._stop), name=name)
            )
        self._tasks.append(
            asyncio.create_task(
                run_escalation(
                    self._alerts,
                    after=timedelta(minutes=settings.auto_escalate_after_minutes),
                    interval_seconds=settings.auto_escalate_interval_seconds,
                    clock=lambda: datetime.now(UTC),
                    stop=self._stop,
                ),
                name="escalation",
            )
        )

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for consumer in self._consumers:
            await consumer.stop()
