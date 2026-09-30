"use client";

import { useQuery } from "@tanstack/react-query";
import { getVitals, type PatientVitals } from "@/lib/api/client";
import { formatClock } from "@/lib/format";
import { QueryState } from "./QueryState";
import { SeverityBadge } from "./SeverityBadge";
import { TimeChart } from "./TimeChart";

export const VITALS_WINDOW_HOURS = 24;
const HOUR_MS = 3_600_000;

/** ICU hour k ends k hours after admission, which is where its alert is drawn. */
export function alertTimes(vitals: PatientVitals): number[] {
  if (!vitals.admitted_at) return [];
  const admitted = new Date(vitals.admitted_at).getTime();
  return vitals.alerts.map((alert) => admitted + alert.icu_hour * HOUR_MS);
}

function PatientDetail({ vitals }: { vitals: PatientVitals }) {
  const markers = alertTimes(vitals);
  const latest = vitals.scores.at(-1);
  const name = vitals.name ? `${vitals.name.given} ${vitals.name.family}` : vitals.mrn;
  const series = Object.entries(vitals.series);
  return (
    <>
      <header className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">{name}</h1>
          <p className="text-slate-600">
            {vitals.mrn}
            {vitals.bed && <> · Bed {vitals.bed}</>}
            {vitals.admitted_at && <> · Admitted {formatClock(vitals.admitted_at)} UTC</>}
          </p>
        </div>
        <SeverityBadge news2={latest?.news2} />
      </header>
      {vitals.encounter_id === null ? (
        <p>This patient has no encounter on record.</p>
      ) : (
        <>
          <section aria-labelledby="alerts-heading" className="mb-6">
            <h2 id="alerts-heading" className="mb-2 text-lg font-semibold">
              Alerts in this window
            </h2>
            {vitals.alerts.length === 0 ? (
              <p className="text-slate-600">No alerts.</p>
            ) : (
              <ul className="flex flex-wrap gap-2">
                {vitals.alerts.map((alert) => (
                  <li
                    key={alert.id}
                    className="rounded border border-red-200 bg-red-50 px-2 py-1 text-sm"
                  >
                    ICU hour {alert.icu_hour}: {alert.source === "news2" ? "NEWS2" : "Model"} alert,{" "}
                    {alert.status}
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-1 text-xs text-slate-500">
              Dashed red lines on the charts mark alerts.
            </p>
          </section>
          <section aria-labelledby="news2-heading" className="mb-6">
            <h2 id="news2-heading" className="mb-2 text-lg font-semibold">
              NEWS2 timeline
            </h2>
            {vitals.scores.length === 0 ? (
              <p className="text-slate-600">No hours have been scored yet.</p>
            ) : (
              <TimeChart
                title="NEWS2 total"
                unit={null}
                step
                markers={markers}
                points={vitals.scores.map((score) => ({
                  at: new Date(score.hour_ending).getTime(),
                  value: score.news2_total,
                }))}
              />
            )}
          </section>
          <section aria-labelledby="vitals-heading">
            <h2 id="vitals-heading" className="mb-2 text-lg font-semibold">
              Vitals, last {vitals.window?.hours ?? VITALS_WINDOW_HOURS} hours
            </h2>
            {series.length === 0 ? (
              <p className="text-slate-600">No observations in this window.</p>
            ) : (
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                {series.map(([code, entry]) => (
                  <TimeChart
                    key={code}
                    title={entry.display}
                    unit={entry.unit}
                    markers={markers}
                    digits={entry.unit === "Cel" ? 1 : 0}
                    points={entry.points.map((point) => ({
                      at: new Date(point.at).getTime(),
                      value: point.value,
                    }))}
                  />
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </>
  );
}

export function PatientView({ mrn }: { mrn: string }) {
  const vitals = useQuery({
    queryKey: ["vitals", mrn],
    queryFn: () => getVitals(mrn, VITALS_WINDOW_HOURS),
  });
  return (
    <QueryState query={vitals} what={`patient ${mrn}`}>
      {(data) => <PatientDetail vitals={data} />}
    </QueryState>
  );
}
