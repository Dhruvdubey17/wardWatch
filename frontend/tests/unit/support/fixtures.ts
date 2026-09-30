import type { AlertView, CensusBed, PatientVitals, ScorePoint } from "@/lib/api/client";

export const NOW = new Date("2026-09-30T12:00:00Z");

const news2 = {
  total: 7,
  single_parameter_three: true,
  components: {
    respiratory_rate: 3,
    spo2: 1,
    supplemental_o2: 0,
    systolic_bp: 1,
    pulse: 1,
    consciousness: 0,
    temperature: 1,
  },
};

export function alertView(overrides: Partial<AlertView> = {}): AlertView {
  return {
    id: "a-1",
    mrn: "MRN-001",
    encounter_id: "E1",
    source: "model",
    status: "open",
    raised_at: "2026-09-30T11:00:00Z",
    icu_hour: 14,
    raw_score: 0.8,
    calibrated_probability: 0.42,
    news2,
    top_factors: [
      {
        feature: "Resp_delta_6h",
        label: "Respiratory rate, change over 6 h",
        unit: "/min",
        value: 8,
        contribution: 0.61,
      },
    ],
    model_version: "xgb-test",
    updated_at: "2026-09-30T11:00:00Z",
    updated_by: null,
    etag: '"1"',
    ...overrides,
  };
}

export function scorePoint(icuHour: number, overrides: Partial<ScorePoint> = {}): ScorePoint {
  return {
    icu_hour: icuHour,
    hour_ending: new Date(Date.UTC(2026, 8, 30, icuHour)).toISOString(),
    news2_total: news2.total,
    news2,
    raw_score: 0.1,
    calibrated_probability: 0.05,
    model_version: "xgb-test",
    alerted: false,
    ...overrides,
  };
}

export function censusBed(overrides: Partial<CensusBed> = {}): CensusBed {
  return {
    bed: "ICU-01",
    encounter_id: "E1",
    patient_id: "P1",
    mrn: "MRN-001",
    name: { family: "Okafor", given: "Ada" },
    admitted_at: "2026-09-29T22:00:00Z",
    vitals: {
      heart_rate: { value: 112, unit: "/min", at: "2026-09-30T11:30:00Z" },
      respiratory_rate: { value: 26, unit: "/min", at: "2026-09-30T11:30:00Z" },
      spo2: { value: 93, unit: "%", at: "2026-09-30T11:30:00Z" },
      systolic_bp: { value: 98, unit: "mm[Hg]", at: "2026-09-30T11:30:00Z" },
      temperature: { value: 38.4, unit: "Cel", at: "2026-09-30T11:30:00Z" },
    },
    latest_score: scorePoint(14, { news2_total: 7, calibrated_probability: 0.42 }),
    trend: [scorePoint(12, { news2_total: 3 }), scorePoint(13, { news2_total: 5 })],
    unresolved_alerts: 1,
    ...overrides,
  };
}

export function patientVitals(overrides: Partial<PatientVitals> = {}): PatientVitals {
  return {
    mrn: "MRN-001",
    encounter_id: "E1",
    patient_id: "P1",
    bed: "ICU-01",
    name: { family: "Okafor", given: "Ada" },
    admitted_at: "2026-09-29T22:00:00Z",
    window: { start: "2026-09-29T22:00:00Z", end: "2026-09-30T12:00:00Z", hours: 24 },
    series: {
      "8867-4": {
        display: "Heart rate",
        unit: "/min",
        points: [
          { at: "2026-09-30T10:00:00Z", value: 98 },
          { at: "2026-09-30T11:00:00Z", value: 112 },
        ],
      },
    },
    scores: [scorePoint(13, { news2_total: 5 }), scorePoint(14, { news2_total: 7, alerted: true })],
    alerts: [
      {
        id: "a-1",
        icu_hour: 14,
        raised_at: "2026-09-30T11:00:00Z",
        source: "model",
        status: "open",
      },
    ],
    ...overrides,
  };
}
