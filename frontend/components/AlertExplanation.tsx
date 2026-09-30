import type { AlertView } from "@/lib/api/client";
import { NEWS2_COMPONENTS, sourceLabel, STATUS_LABEL } from "@/lib/alerts";
import { formatDuration, formatFactor, formatProbability } from "@/lib/format";
import type { ReactNode } from "react";
import { SeverityBadge } from "./SeverityBadge";

export function AlertExplanation({
  alert,
  now,
  children,
}: {
  alert: AlertView;
  now: Date;
  children?: ReactNode;
}) {
  return (
    <section
      aria-labelledby={`alert-${alert.id}-heading`}
      className="rounded-lg border border-slate-200 bg-white p-4"
    >
      <header className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 id={`alert-${alert.id}-heading`} className="text-lg font-semibold">
            {sourceLabel(alert.source)} alert for {alert.mrn}
          </h2>
          <p className="text-sm text-slate-600">
            ICU hour {alert.icu_hour} · {STATUS_LABEL[alert.status] ?? alert.status} · open for{" "}
            {formatDuration(alert.raised_at, now)}
            {alert.updated_by && <> · last changed by {alert.updated_by}</>}
          </p>
        </div>
        <SeverityBadge news2={alert.news2} />
      </header>
      <dl className="mb-4 grid grid-cols-2 gap-2 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-slate-500">Calibrated probability</dt>
          <dd className="font-semibold">{formatProbability(alert.calibrated_probability)}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Model version</dt>
          <dd>{alert.model_version}</dd>
        </div>
      </dl>
      <h3 className="mb-1 font-medium">Top contributing factors</h3>
      {alert.top_factors.length === 0 ? (
        <p className="mb-4 text-sm text-slate-600">
          This alert came from the NEWS2 score, so there are no model factors.
        </p>
      ) : (
        <ol className="mb-4 list-decimal pl-5 text-sm">
          {alert.top_factors.map((factor) => (
            <li key={factor.feature}>{formatFactor(factor)}</li>
          ))}
        </ol>
      )}
      <table className="mb-4 w-full max-w-sm text-left text-sm">
        <caption className="mb-1 text-left font-medium">
          NEWS2 breakdown, total {alert.news2.total}
        </caption>
        <thead className="sr-only">
          <tr>
            <th scope="col">Parameter</th>
            <th scope="col">Points</th>
          </tr>
        </thead>
        <tbody>
          {NEWS2_COMPONENTS.map(({ key, label }) => (
            <tr key={key} className={alert.news2.components[key] >= 3 ? "font-semibold" : ""}>
              <th scope="row" className="font-normal">
                {label}
              </th>
              <td>{alert.news2.components[key]}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {children}
    </section>
  );
}
