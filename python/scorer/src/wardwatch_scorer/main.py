"""wardwatch-scorer: consume fhir.observations, publish ward.scores and ward.alerts."""

import asyncio
import json
import logging
import signal
import sys
from datetime import UTC, datetime

import httpx
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from prometheus_client import start_http_server
from wardwatch_ml.bundle import ServingBundle

from wardwatch_scorer.engine import ScoringEngine
from wardwatch_scorer.online import OnlineScorer
from wardwatch_scorer.rebuild import rebuild
from wardwatch_scorer.service import ScorerService
from wardwatch_scorer.settings import Settings

log = logging.getLogger("wardwatch_scorer")
REBUILD_ATTEMPTS = 30


def load_bundle(settings: Settings) -> ServingBundle | None:
    if settings.bundle_dir is None or not (settings.bundle_dir / "bundle.json").is_file():
        log.warning(
            "no serving bundle at %s; scoring NEWS2 only. Run `make train` to create one.",
            settings.bundle_dir,
        )
        return None
    bundle = ServingBundle.load(settings.bundle_dir.resolve())
    log.info("loaded serving bundle %s", bundle.model_version)
    return bundle


async def rebuild_with_retry(engine: ScoringEngine, settings: Settings) -> None:
    async with httpx.AsyncClient(base_url=settings.fhir_base_url, timeout=10) as client:
        for attempt in range(1, REBUILD_ATTEMPTS + 1):
            try:
                await rebuild(engine, client)
            except httpx.HTTPError as error:
                log.warning(
                    "FHIR service not reachable for rebuild (attempt %d): %s", attempt, error
                )
                await asyncio.sleep(2)
                continue
            return
    log.warning("starting without a rebuild; windows fill from new observations only")


async def serve(settings: Settings) -> None:
    engine = ScoringEngine(
        OnlineScorer(load_bundle(settings)),
        settle_seconds=settings.settle_seconds,
        wall=lambda: datetime.now(UTC),
    )
    await rebuild_with_retry(engine, settings)
    consumer = AIOKafkaConsumer(
        settings.observations_topic,
        bootstrap_servers=settings.kafka_bootstrap,
        group_id=settings.consumer_group,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap, enable_idempotence=True, acks="all"
    )
    await consumer.start()
    await producer.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signal_number, stop.set)
    try:
        await ScorerService(settings, engine, consumer, producer).run(stop)
    finally:
        await consumer.stop()
        await producer.stop()


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
                "level": record.levelname.lower(),
                "logger": record.name,
                "message": record.getMessage(),
            }
        )


def main() -> int:
    settings = Settings()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logging.basicConfig(level=settings.log_level, handlers=[handler])
    start_http_server(settings.metrics_port, addr=settings.metrics_bind)
    asyncio.run(serve(settings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
