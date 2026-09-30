import type { News2 } from "./api/client";

export type Severity = "high" | "medium" | "low-medium" | "low" | "unknown";

/** NEWS2 clinical risk bands from the RCP 2017 chart. */
export function news2Severity(news2: News2 | null | undefined): Severity {
  if (!news2) return "unknown";
  if (news2.total >= 7) return "high";
  if (news2.total >= 5) return "medium";
  if (news2.single_parameter_three) return "low-medium";
  return "low";
}

export const SEVERITY_RANK: Record<Severity, number> = {
  high: 3,
  medium: 2,
  "low-medium": 1,
  low: 0,
  unknown: -1,
};

// Each band has a text label and a distinct symbol, so color is never the only cue.
export const SEVERITY_STYLE: Record<
  Severity,
  { label: string; symbol: string; className: string }
> = {
  high: { label: "High", symbol: "▲", className: "bg-red-700 text-white border-red-700" },
  medium: {
    label: "Medium",
    symbol: "◆",
    className: "bg-amber-400 text-slate-950 border-amber-500",
  },
  "low-medium": {
    label: "Low-medium",
    symbol: "●",
    className: "bg-yellow-200 text-slate-950 border-yellow-400",
  },
  low: {
    label: "Low",
    symbol: "○",
    className: "bg-emerald-100 text-emerald-950 border-emerald-300",
  },
  unknown: {
    label: "Not scored",
    symbol: "--",
    className: "bg-slate-100 text-slate-700 border-slate-300",
  },
};
