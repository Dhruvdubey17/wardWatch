"use client";

import type { QueryKey } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import {
  ApiError,
  ESCALATION_REASONS,
  type AlertAction,
  type AlertView,
  type EscalationReason,
} from "@/lib/api/client";
import { ALLOWED_ACTIONS, useAlertAction } from "@/hooks/useAlertAction";

export const REASON_LABEL: Record<EscalationReason, string> = {
  clinical_deterioration: "Clinical deterioration",
  senior_review_needed: "Senior review needed",
  sepsis_pathway_started: "Sepsis pathway started",
  critical_care_outreach: "Critical care outreach",
  other: "Other",
};

const ACTION_LABEL: Record<AlertAction["kind"], string> = {
  acknowledge: "Acknowledge",
  escalate: "Escalate",
  resolve: "Resolve",
};

const ACTOR_STORAGE_KEY = "wardwatch.actor";

function storedActor(): string {
  try {
    return window.localStorage.getItem(ACTOR_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

function storeActor(actor: string): void {
  try {
    window.localStorage.setItem(ACTOR_STORAGE_KEY, actor);
  } catch {
    // Storage can be unavailable (private windows); the name is then asked for again.
  }
}

type Feedback = { tone: "error" | "done"; text: string } | null;

export function AlertActions({ alert, listKey }: { alert: AlertView; listKey: QueryKey }) {
  const mutation = useAlertAction(listKey);
  // Rendered only after the alert list loads in the browser, so storage is readable here.
  const [actor, setActor] = useState(storedActor);
  const [note, setNote] = useState("");
  const [reason, setReason] = useState<EscalationReason | "">("");
  const [feedback, setFeedback] = useState<Feedback>(null);

  const allowed = ALLOWED_ACTIONS[alert.status] ?? [];
  if (allowed.length === 0) return null;

  function submit(kind: AlertAction["kind"]) {
    return (event: FormEvent) => {
      event.preventDefault();
      const name = actor.trim();
      if (!name) {
        setFeedback({ tone: "error", text: "Enter your name before changing an alert." });
        return;
      }
      if (kind === "escalate" && !reason) {
        setFeedback({ tone: "error", text: "Choose a reason for escalating." });
        return;
      }
      storeActor(name);
      const input = { actor: name, note: note.trim() || null };
      const action: AlertAction =
        kind === "escalate" && reason
          ? { kind, input: { ...input, reason } }
          : { kind: kind as "acknowledge" | "resolve", input };
      setFeedback(null);
      mutation.mutate(
        { alert, action },
        {
          onSuccess: (updated) =>
            setFeedback({ tone: "done", text: `Alert ${updated.status} by ${name}.` }),
          onError: (error) =>
            setFeedback({
              tone: "error",
              text:
                error instanceof ApiError && error.isConflict
                  ? `Not saved: ${error.message}. The alert has been refreshed.`
                  : `Not saved: ${error.message}. Your change was undone.`,
            }),
        },
      );
    };
  }

  return (
    <div className="flex flex-col gap-3 border-t border-slate-200 pt-4">
      <h3 className="font-medium">Workflow</h3>
      <div className="flex flex-wrap gap-4">
        <label className="flex flex-col text-sm">
          Your name
          <input
            value={actor}
            onChange={(event) => setActor(event.target.value)}
            autoComplete="name"
            className="rounded border border-slate-300 px-2 py-1"
          />
        </label>
        <label className="flex grow flex-col text-sm">
          Note (optional)
          <textarea
            value={note}
            onChange={(event) => setNote(event.target.value)}
            maxLength={2000}
            rows={2}
            className="rounded border border-slate-300 px-2 py-1"
          />
        </label>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        {allowed.map((kind) => (
          <form key={kind} onSubmit={submit(kind)} className="flex items-end gap-2">
            {kind === "escalate" && (
              <label className="flex flex-col text-sm">
                Escalation reason
                <select
                  value={reason}
                  onChange={(event) => setReason(event.target.value as EscalationReason | "")}
                  className="rounded border border-slate-300 px-2 py-1"
                >
                  <option value="">Choose a reason</option>
                  {ESCALATION_REASONS.map((value) => (
                    <option key={value} value={value}>
                      {REASON_LABEL[value]}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <button
              type="submit"
              disabled={mutation.isPending}
              className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              {ACTION_LABEL[kind]}
            </button>
          </form>
        ))}
      </div>
      <p
        role={feedback?.tone === "error" ? "alert" : "status"}
        className={feedback?.tone === "error" ? "text-sm text-red-800" : "text-sm text-emerald-800"}
      >
        {feedback?.text}
      </p>
    </div>
  );
}
