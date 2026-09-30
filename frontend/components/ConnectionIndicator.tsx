"use client";

import { useLiveUpdates, type ConnectionState } from "@/hooks/useLiveUpdates";

const LOOK: Record<ConnectionState, { text: string; dot: string }> = {
  connecting: { text: "Connecting to live updates", dot: "bg-slate-400" },
  open: { text: "Live", dot: "bg-emerald-600" },
  reconnecting: { text: "Live updates lost, reconnecting", dot: "bg-red-600" },
};

export function ConnectionIndicator() {
  const state = useLiveUpdates();
  const look = LOOK[state];
  return (
    <p role="status" className="ml-auto flex items-center gap-2 text-sm" data-state={state}>
      <span aria-hidden="true" className={`inline-block h-2.5 w-2.5 rounded-full ${look.dot}`} />
      {look.text}
    </p>
  );
}
