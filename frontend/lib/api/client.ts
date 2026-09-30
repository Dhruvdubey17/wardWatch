import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

type Schemas = components["schemas"];
export type AlertView = Schemas["AlertView"];
export type CensusBed = Schemas["CensusBed"];
export type PatientVitals = Schemas["PatientVitals"];
export type ScorePoint = Schemas["ScorePoint"];
export type Factor = Schemas["Factor"];
export type News2 = Schemas["News2"];
export type AlertStatus = "open" | "acknowledged" | "escalated" | "resolved";

export const ESCALATION_REASONS = [
  "clinical_deterioration",
  "senior_review_needed",
  "sepsis_pathway_started",
  "critical_care_outreach",
  "other",
] as const;
export type EscalationReason = (typeof ESCALATION_REASONS)[number];

/** A failed request. `status` is 0 when the service could not be reached. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    /** The alert as the server holds it now, sent with 409 and 412. */
    readonly current: AlertView | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }

  get isConflict(): boolean {
    return this.status === 409 || this.status === 412;
  }
}

function baseUrl(): string {
  return typeof window === "undefined"
    ? (process.env.WARDWATCH_API_URL ?? "http://127.0.0.1:8000")
    : window.location.origin;
}

// fetch is looked up per call so test interceptors installed later still apply.
const http = createClient<paths>({
  baseUrl: baseUrl(),
  fetch: (request) => globalThis.fetch(request),
});

function describe(error: unknown, status: number): ApiError {
  if (error && typeof error === "object" && "detail" in error) {
    const { detail } = error as { detail: unknown; alert?: AlertView | null };
    const current = (error as { alert?: AlertView | null }).alert ?? null;
    if (typeof detail === "string") return new ApiError(status, detail, current);
    if (Array.isArray(detail)) {
      const messages = detail.map((item: { msg?: string }) => item.msg ?? "invalid request");
      return new ApiError(status, messages.join("; "));
    }
  }
  return new ApiError(status, `request failed with status ${status}`);
}

async function call<T>(
  request: () => Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<{ data: T; response: Response }> {
  let result;
  try {
    result = await request();
  } catch (cause) {
    throw new ApiError(0, `the WardWatch service is unreachable (${String(cause)})`);
  }
  const { data, error, response } = result;
  if (!response.ok || data === undefined) throw describe(error, response.status);
  return { data, response };
}

export async function getCensus(): Promise<CensusBed[]> {
  return (await call(() => http.GET("/api/ward/census"))).data;
}

export async function listAlerts(statuses: AlertStatus[] = []): Promise<AlertView[]> {
  const query = statuses.length ? { status: statuses.join(",") } : {};
  return (await call(() => http.GET("/api/alerts", { params: { query } }))).data;
}

export async function getAlert(alertId: string): Promise<AlertView> {
  const path = { alert_id: alertId };
  return (await call(() => http.GET("/api/alerts/{alert_id}", { params: { path } }))).data;
}

export async function getVitals(mrn: string, hours?: number): Promise<PatientVitals> {
  const params = { path: { mrn }, query: hours === undefined ? {} : { hours } };
  return (await call(() => http.GET("/api/patients/{mrn}/vitals", { params }))).data;
}

export interface ActionInput {
  actor: string;
  note?: string | null;
}

export type AlertAction =
  | { kind: "acknowledge"; input: ActionInput }
  | { kind: "escalate"; input: ActionInput & { reason: EscalationReason } }
  | { kind: "resolve"; input: ActionInput };

/** Apply a workflow action to the version of the alert the user was looking at. */
export async function actOnAlert(alert: AlertView, action: AlertAction): Promise<AlertView> {
  const params = { path: { alert_id: alert.id }, header: { "if-match": alert.etag } };
  const base = { actor: action.input.actor, note: action.input.note || null };
  switch (action.kind) {
    case "acknowledge":
      return (
        await call(() => http.POST("/api/alerts/{alert_id}/acknowledge", { params, body: base }))
      ).data;
    case "escalate": {
      const body = { ...base, reason: action.input.reason };
      return (await call(() => http.POST("/api/alerts/{alert_id}/escalate", { params, body })))
        .data;
    }
    case "resolve":
      return (await call(() => http.POST("/api/alerts/{alert_id}/resolve", { params, body: base })))
        .data;
  }
}
