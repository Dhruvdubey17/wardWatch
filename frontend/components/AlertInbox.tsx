"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { listAlerts, type AlertView } from "@/lib/api/client";
import { alertSeverity, sortAlerts, sourceLabel, STATUS_LABEL, UNRESOLVED } from "@/lib/alerts";
import { formatDuration, formatProbability } from "@/lib/format";
import { SEVERITY_STYLE } from "@/lib/severity";
import { useNow } from "@/hooks/useNow";
import { AlertActions } from "./AlertActions";
import { AlertExplanation } from "./AlertExplanation";
import { QueryState } from "./QueryState";

export const INBOX_QUERY_KEY = ["alerts", "unresolved"] as const;

function AlertRow({
  alert,
  now,
  selected,
  onSelect,
}: {
  alert: AlertView;
  now: Date;
  selected: boolean;
  onSelect: () => void;
}) {
  const style = SEVERITY_STYLE[alertSeverity(alert)];
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onSelect}
      className={`w-full rounded border p-3 text-left ${selected ? "border-slate-900 bg-slate-100" : "border-slate-200 bg-white"}`}
    >
      <span className={`mr-2 rounded border px-1.5 text-xs font-semibold ${style.className}`}>
        <span aria-hidden="true">{style.symbol} </span>
        {style.label}
      </span>
      <span className="font-medium">{alert.mrn}</span>
      <span className="block text-sm text-slate-600">
        {sourceLabel(alert.source)} · {formatProbability(alert.calibrated_probability)} ·{" "}
        {STATUS_LABEL[alert.status] ?? alert.status} · open {formatDuration(alert.raised_at, now)}
      </span>
    </button>
  );
}

export function AlertInbox() {
  const alerts = useQuery({
    queryKey: INBOX_QUERY_KEY,
    queryFn: () => listAlerts([...UNRESOLVED]),
    select: sortAlerts,
  });
  const now = useNow();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  return (
    <QueryState query={alerts} what="alerts">
      {(sorted) => {
        if (sorted.length === 0) return <p className="text-slate-600">No unresolved alerts.</p>;
        const selected = sorted.find((alert) => alert.id === selectedId) ?? sorted[0]!;
        return (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
            <ol aria-label="Unresolved alerts, most severe first" className="flex flex-col gap-2">
              {sorted.map((alert) => (
                <li key={alert.id}>
                  <AlertRow
                    alert={alert}
                    now={now}
                    selected={alert.id === selected.id}
                    onSelect={() => setSelectedId(alert.id)}
                  />
                </li>
              ))}
            </ol>
            <AlertExplanation alert={selected} now={now}>
              {/* Keyed by alert so the form starts empty for each alert. */}
              <AlertActions key={selected.id} alert={selected} listKey={INBOX_QUERY_KEY} />
            </AlertExplanation>
          </div>
        );
      }}
    </QueryState>
  );
}
