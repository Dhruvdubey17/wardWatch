"""Database tables. Each FHIR resource is kept whole as JSONB next to the
columns the API filters on."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# JSONB on Postgres, plain JSON elsewhere.
JsonColumn = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mrn: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    family: Mapped[str] = mapped_column(String(128))
    given: Mapped[str] = mapped_column(String(128))
    birth_date: Mapped[date | None] = mapped_column(Date)
    gender: Mapped[str] = mapped_column(String(16))
    resource: Mapped[dict[str, Any]] = mapped_column(JsonColumn)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Encounter(Base):
    __tablename__ = "encounters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    status: Mapped[str] = mapped_column(String(32))
    bed: Mapped[str] = mapped_column(String(64))
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resource: Mapped[dict[str, Any]] = mapped_column(JsonColumn)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (
        Index("ix_observations_patient_code_effective", "patient_id", "code", "effective"),
    )

    # MSH-10 and the OBX set ID, so a replayed message overwrites itself.
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    encounter_id: Mapped[str] = mapped_column(ForeignKey("encounters.id"), index=True)
    code: Mapped[str] = mapped_column(String(32))
    category: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    effective: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(32))
    message_time: Mapped[str] = mapped_column(String(64))
    resource: Mapped[dict[str, Any]] = mapped_column(JsonColumn)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mrn: Mapped[str] = mapped_column(String(64), index=True)
    encounter_id: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(16))
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    icu_hour: Mapped[int] = mapped_column(Integer)
    raw_score: Mapped[float] = mapped_column(Float)
    calibrated_probability: Mapped[float | None] = mapped_column(Float)
    news2: Mapped[dict[str, Any]] = mapped_column(JsonColumn)
    top_factors: Mapped[list[dict[str, Any]]] = mapped_column(JsonColumn)
    model_version: Mapped[str] = mapped_column(String(64))
    message_time: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), index=True)
    # Bumped on every transition; the ETag is derived from it.
    version: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(String(128))


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    alert_id: Mapped[str] = mapped_column(ForeignKey("alerts.id"), index=True)
    transition: Mapped[str] = mapped_column(String(16))
    from_status: Mapped[str] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16))
    actor: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Score(Base):
    __tablename__ = "scores"

    encounter_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    icu_hour: Mapped[int] = mapped_column(Integer, primary_key=True)
    mrn: Mapped[str] = mapped_column(String(64), index=True)
    hour_ending: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    news2_total: Mapped[int] = mapped_column(Integer)
    news2: Mapped[dict[str, Any]] = mapped_column(JsonColumn)
    raw_score: Mapped[float | None] = mapped_column(Float)
    calibrated_probability: Mapped[float | None] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String(64))
    alerted: Mapped[bool] = mapped_column(Boolean)
