import type { AlertView, News2 } from "./api/client";
import { news2Severity, SEVERITY_RANK, type Severity } from "./severity";

export const UNRESOLVED = ["open", "acknowledged", "escalated"] as const;

/**
 * NEWS2 band at the alerting hour. A model alert ranks at least medium, since
 * its operating point was set to match the alert burden of NEWS2 >= 5.
 */
export function alertSeverity(alert: AlertView): Severity {
  const band = news2Severity(alert.news2);
  if (alert.source !== "news2" && SEVERITY_RANK[band] < SEVERITY_RANK.medium) return "medium";
  return band;
}

/** Most severe first; within a band the alert open longest comes first. */
export function sortAlerts(alerts: readonly AlertView[]): AlertView[] {
  return [...alerts].sort(
    (a, b) =>
      SEVERITY_RANK[alertSeverity(b)] - SEVERITY_RANK[alertSeverity(a)] ||
      Date.parse(a.raised_at) - Date.parse(b.raised_at) ||
      a.id.localeCompare(b.id),
  );
}

export const NEWS2_COMPONENTS: { key: keyof News2["components"]; label: string }[] = [
  { key: "respiratory_rate", label: "Respiratory rate" },
  { key: "spo2", label: "SpO2" },
  { key: "supplemental_o2", label: "Supplemental oxygen" },
  { key: "systolic_bp", label: "Systolic blood pressure" },
  { key: "pulse", label: "Pulse" },
  { key: "consciousness", label: "Consciousness" },
  { key: "temperature", label: "Temperature" },
];

export function sourceLabel(source: string): string {
  return source === "news2" ? "NEWS2" : "Sepsis model";
}

export const STATUS_LABEL: Record<string, string> = {
  open: "Open",
  acknowledged: "Acknowledged",
  escalated: "Escalated",
  resolved: "Resolved",
};
