"""Response models for the ward API. They are also what the dashboard's
TypeScript types are generated from, through contracts/openapi.json."""

from typing import Literal

from pydantic import BaseModel


class News2Components(BaseModel):
    respiratory_rate: int
    spo2: int
    supplemental_o2: int
    systolic_bp: int
    pulse: int
    consciousness: int
    temperature: int


class News2(BaseModel):
    total: int
    components: News2Components
    single_parameter_three: bool


class Factor(BaseModel):
    feature: str
    label: str
    unit: str
    value: float | None
    contribution: float


class AlertView(BaseModel):
    id: str
    mrn: str
    encounter_id: str
    source: Literal["model", "news2"]
    status: Literal["open", "acknowledged", "escalated", "resolved"]
    raised_at: str
    icu_hour: int
    raw_score: float
    calibrated_probability: float | None
    news2: News2
    top_factors: list[Factor]
    model_version: str
    updated_at: str
    updated_by: str | None
    etag: str


class ErrorBody(BaseModel):
    detail: str
    alert: AlertView | None = None


class PersonName(BaseModel):
    family: str
    given: str


class VitalReading(BaseModel):
    value: float | None
    unit: str | None
    at: str


class ScorePoint(BaseModel):
    icu_hour: int
    hour_ending: str
    news2_total: int
    news2: News2
    raw_score: float | None
    calibrated_probability: float | None
    model_version: str
    alerted: bool


class CensusBed(BaseModel):
    bed: str
    encounter_id: str
    patient_id: str
    mrn: str
    name: PersonName
    admitted_at: str | None
    vitals: dict[str, VitalReading]
    latest_score: ScorePoint | None
    trend: list[ScorePoint]
    unresolved_alerts: int


class SeriesPoint(BaseModel):
    at: str
    value: float | None


class Series(BaseModel):
    display: str
    unit: str | None
    points: list[SeriesPoint]


class VitalsWindow(BaseModel):
    start: str | None
    end: str | None
    hours: int


class AlertMarker(BaseModel):
    id: str
    icu_hour: int
    raised_at: str
    source: Literal["model", "news2"]
    status: Literal["open", "acknowledged", "escalated", "resolved"]


class PatientVitals(BaseModel):
    mrn: str
    patient_id: str | None = None
    name: PersonName | None = None
    encounter_id: str | None
    bed: str | None = None
    admitted_at: str | None = None
    window: VitalsWindow | None = None
    series: dict[str, Series]
    scores: list[ScorePoint]
    alerts: list[AlertMarker]
