import Link from "next/link";
import type { CensusBed } from "@/lib/api/client";
import { formatNumber, formatProbability } from "@/lib/format";
import { news2Severity } from "@/lib/severity";
import { SeverityBadge } from "./SeverityBadge";
import { Sparkline } from "./Sparkline";

const TILE_VITALS = [
  { key: "heart_rate", label: "HR", digits: 0 },
  { key: "respiratory_rate", label: "RR", digits: 0 },
  { key: "spo2", label: "SpO2", digits: 0 },
  { key: "systolic_bp", label: "SBP", digits: 0 },
  { key: "temperature", label: "Temp", digits: 1 },
] as const;

const BORDER = {
  high: "border-l-red-700",
  medium: "border-l-amber-500",
  "low-medium": "border-l-yellow-400",
  low: "border-l-emerald-400",
  unknown: "border-l-slate-300",
} as const;

export function BedTile({ bed }: { bed: CensusBed }) {
  const score = bed.latest_score;
  const severity = news2Severity(score?.news2);
  const fullName = `${bed.name.given} ${bed.name.family}`;
  return (
    <article
      aria-label={`Bed ${bed.bed}, ${fullName}`}
      className={`flex flex-col gap-3 rounded-lg border border-l-8 border-slate-200 bg-white p-4 shadow-sm ${BORDER[severity]}`}
    >
      <header className="flex items-start justify-between gap-2">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Bed {bed.bed}
          </p>
          <h2 className="text-lg font-semibold">
            <Link
              href={`/patients/${encodeURIComponent(bed.mrn)}`}
              className="underline-offset-2 hover:underline focus-visible:outline-2"
            >
              {fullName}
            </Link>
          </h2>
          <p className="text-sm text-slate-600">{bed.mrn}</p>
        </div>
        <SeverityBadge news2={score?.news2} />
      </header>
      <dl className="grid grid-cols-5 gap-1 text-center">
        {TILE_VITALS.map(({ key, label, digits }) => {
          const reading = bed.vitals[key];
          return (
            <div key={key}>
              <dt className="text-xs text-slate-500">{label}</dt>
              <dd className="font-mono text-base">{formatNumber(reading?.value, digits)}</dd>
            </div>
          );
        })}
      </dl>
      <div className="flex items-center justify-between gap-2 text-sm">
        <span>
          Model risk <strong>{formatProbability(score?.calibrated_probability)}</strong>
        </span>
        {bed.unresolved_alerts > 0 && (
          <span className="rounded bg-red-50 px-2 py-0.5 font-medium text-red-800">
            {bed.unresolved_alerts} unresolved {bed.unresolved_alerts === 1 ? "alert" : "alerts"}
          </span>
        )}
      </div>
      <Sparkline
        values={bed.trend.map((point) => point.news2_total)}
        label="NEWS2, last 12 hours"
      />
    </article>
  );
}
