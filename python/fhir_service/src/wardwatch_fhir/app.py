"""The FastAPI application and its wiring."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiokafka import AIOKafkaProducer
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from wardwatch_fhir import fhir_api, ops_api, ward_api
from wardwatch_fhir.alert_service import AlertService
from wardwatch_fhir.db import create_engine, session_factory
from wardwatch_fhir.events import EventBus
from wardwatch_fhir.fhir_query import SearchParameterError
from wardwatch_fhir.settings import Settings
from wardwatch_fhir.workers import Workers


def create_app(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession] | None = None,
    producer: AIOKafkaProducer | None = None,
) -> FastAPI:
    """Build the app. Pass `sessions` and `producer` to reuse existing ones, as tests do."""
    bus = EventBus()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = None
        if sessions is None:
            engine = create_engine(settings.database_url)
            app.state.sessions = session_factory(engine)
        else:
            app.state.sessions = sessions
        owned_producer = None
        workers = None
        app.state.producer = producer
        try:
            if settings.run_consumers and producer is None:
                owned_producer = AIOKafkaProducer(
                    bootstrap_servers=settings.kafka_bootstrap, enable_idempotence=True, acks="all"
                )
                await owned_producer.start()
                app.state.producer = owned_producer
            app.state.alerts = AlertService(
                app.state.sessions, bus, app.state.producer, settings.alert_events_topic
            )
            if settings.run_consumers:
                workers = Workers(
                    settings, app.state.sessions, bus, app.state.producer, app.state.alerts
                )
                await workers.start()
            await app.state.alerts.refresh_open_gauge()
            yield
        finally:
            # Also runs when startup fails part way, so nothing is left open.
            if workers is not None:
                await workers.stop()
            if owned_producer is not None:
                await owned_producer.stop()
            if engine is not None:
                await engine.dispose()

    app = FastAPI(title="WardWatch FHIR service", lifespan=lifespan)
    app.state.settings = settings
    app.state.bus = bus
    app.state.producer = producer
    if sessions is not None:
        app.state.sessions = sessions
        app.state.alerts = AlertService(sessions, bus, producer, settings.alert_events_topic)
    app.include_router(fhir_api.router)
    app.include_router(ward_api.router)
    app.include_router(ops_api.router)

    @app.exception_handler(SearchParameterError)
    async def search_error(request: Request, error: SearchParameterError) -> JSONResponse:
        return fhir_api.operation_outcome(400, "invalid", str(error))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        if request.url.path.startswith("/fhir"):
            return fhir_api.operation_outcome(400, "invalid", str(error.errors()))
        return JSONResponse({"detail": error.errors()}, status_code=422)

    return app
