"use client";

import { useQuery } from "@tanstack/react-query";
import { useState, type KeyboardEvent } from "react";
import { getCensus, listAlerts, type AlertView, type CensusBed } from "@/lib/api/client";
import {
  alertSeverity,
  patientLabel,
  sortAlerts,
  sourceLabel,
  STATUS_LABEL,
  UNRESOLVED,
} from "@/lib/alerts";
import { formatDuration, formatProbability } from "@/lib/format";
import { SEVERITY_STYLE } from "@/lib/severity";
import { useNow } from "@/hooks/useNow";
import { AlertActions } from "./AlertActions";
import { AlertExplanation } from "./AlertExplanation";
import { QueryState } from "./QueryState";

export const INBOX_QUERY_KEY = ["alerts", "unresolved"] as const;

function AlertRow({
  alert,
  patient,
  now,
  selected,
  onSelect,
}: {
  alert: AlertView;
  patient: CensusBed | undefined;
  now: Date;
  selected: boolean;
  onSelect: () => void;
}) {
  const style = SEVERITY_STYLE[alertSeverity(alert)];
  return (
    <button
      type="button"
      aria-pressed={selected}
      data-alert-id={alert.id}
      // Only the selected row is a tab stop; the arrow keys move within the list.
      tabIndex={selected ? 0 : -1}
      onClick={onSelect}
      className={`w-full rounded border p-3 text-left ${selected ? "border-slate-900 bg-slate-100" : "border-slate-200 bg-white"}`}
    >
      <span className={`mr-2 rounded border px-1.5 text-xs font-semibold ${style.className}`}>
        <span aria-hidden="true">{style.symbol} </span>
        {style.label}
      </span>
      <span className="font-medium">{patientLabel(alert, patient)}</span>
      <span className="block text-sm text-slate-600">
        {sourceLabel(alert.source)} · {formatProbability(alert.calibrated_probability)} ·{" "}
        {STATUS_LABEL[alert.status] ?? alert.status} · open {formatDuration(alert.raised_at, now)}
      </span>
    </button>
  );
}

const MOVES: Record<string, (index: number, count: number) => number> = {
  ArrowDown: (index, count) => Math.min(count - 1, index + 1),
  ArrowUp: (index) => Math.max(0, index - 1),
  Home: () => 0,
  End: (_index, count) => count - 1,
};

/** Arrow keys, Home and End move through the list and select as they go. */
function moveThroughList(event: KeyboardEvent<HTMLOListElement>, select: (id: string) => void) {
  const move = MOVES[event.key];
  if (!move) return;
  const rows = [
    ...event.currentTarget.querySelectorAll<HTMLButtonElement>("button[data-alert-id]"),
  ];
  const index = rows.findIndex((row) => row === document.activeElement);
  if (index < 0) return;
  event.preventDefault();
  const target = rows[move(index, rows.length)]!;
  target.focus();
  select(target.dataset.alertId!);
}

export function AlertInbox() {
  const alerts = useQuery({
    queryKey: INBOX_QUERY_KEY,
    queryFn: () => listAlerts([...UNRESOLVED]),
    select: sortAlerts,
  });
  // The census is usually cached from the ward board; it only adds names and beds.
  const census = useQuery({ queryKey: ["census"], queryFn: getCensus });
  const patients = new Map(census.data?.map((bed) => [bed.mrn, bed]));
  const now = useNow();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  return (
    <QueryState query={alerts} what="alerts">
      {(sorted) => {
        if (sorted.length === 0) return <p className="text-slate-600">No unresolved alerts.</p>;
        const selected = sorted.find((alert) => alert.id === selectedId) ?? sorted[0]!;
        return (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
            <ol
              aria-label="Unresolved alerts, most severe first"
              aria-describedby="inbox-keys"
              className="flex flex-col gap-2"
              onKeyDown={(event) => moveThroughList(event, setSelectedId)}
            >
              {sorted.map((alert) => (
                <li key={alert.id}>
                  <AlertRow
                    alert={alert}
                    patient={patients.get(alert.mrn)}
                    now={now}
                    selected={alert.id === selected.id}
                    onSelect={() => setSelectedId(alert.id)}
                  />
                </li>
              ))}
            </ol>
            <p id="inbox-keys" className="sr-only">
              Use the up and down arrow keys to move between alerts.
            </p>
            <AlertExplanation alert={selected} patient={patients.get(selected.mrn)} now={now}>
              {/* Keyed by alert so the form starts empty for each alert. */}
              <AlertActions key={selected.id} alert={selected} listKey={INBOX_QUERY_KEY} />
            </AlertExplanation>
          </div>
        );
      }}
    </QueryState>
  );
}
