import type { Factor } from "./api/client";

export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  return value.toFixed(digits);
}

export function formatProbability(probability: number | null | undefined): string {
  if (probability === null || probability === undefined) return "--";
  return `${Math.round(probability * 100)}%`;
}

/** "2 h 15 min" from a start time to now. */
export function formatDuration(from: string, now: Date): string {
  const minutes = Math.max(0, Math.floor((now.getTime() - new Date(from).getTime()) / 60_000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`;
  return `${Math.floor(hours / 24)} d ${hours % 24} h`;
}

export function formatClock(at: string): string {
  return new Date(at).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  });
}

function withUnit(value: string, unit: string): string {
  if (!unit) return value;
  if (unit === "/min" || unit === "%") return `${value}${unit}`;
  return `${value} ${unit}`;
}

// Whole numbers and values of 100 or more need no decimals; a temperature of
// 38.9 must not read as 39.
function digitsFor(value: number): number {
  return Number.isInteger(value) || Math.abs(value) >= 100 ? 0 : 1;
}

/**
 * A model factor in plain language, for example "Respiratory rate up 8/min over 6 h"
 * or "Lactate 3.1 mmol/L".
 */
export function formatFactor(factor: Factor): string {
  const [name = factor.label, detail = ""] = factor.label.split(", ");
  if (factor.value === null) return `${name}: not measured`;
  const change = /^change over (\d+) h$/.exec(detail);
  if (change) {
    if (factor.value === 0) return `${name} unchanged over ${change[1]} h`;
    const direction = factor.value > 0 ? "up" : "down";
    const amount = formatNumber(Math.abs(factor.value), digitsFor(factor.value));
    return `${name} ${direction} ${withUnit(amount, factor.unit)} over ${change[1]} h`;
  }
  const value = withUnit(formatNumber(factor.value, digitsFor(factor.value)), factor.unit);
  if (!detail || detail === "latest") return `${name} ${value}`;
  if (detail === "hours since last measured") return `${name} last measured ${value} ago`;
  if (detail === "measured this hour")
    return factor.value ? `${name} measured this hour` : `${name} not measured this hour`;
  return `${name} ${value} (${detail})`;
}
