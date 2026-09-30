"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

export type ConnectionState = "connecting" | "open" | "reconnecting";

export const STREAM_URL = "/api/stream";
const FIRST_RETRY_MS = 1_000;
const MAX_RETRY_MS = 30_000;

/** Wait before reconnect attempt `attempt` (0-based): 1 s, 2 s, 4 s, up to 30 s. */
export function backoffMs(attempt: number): number {
  return Math.min(MAX_RETRY_MS, FIRST_RETRY_MS * 2 ** attempt);
}

interface StreamEvent {
  mrn?: string;
}

function parse(message: MessageEvent<string>): StreamEvent {
  try {
    return JSON.parse(message.data) as StreamEvent;
  } catch {
    return {};
  }
}

/**
 * Subscribes to the FHIR service's event stream and refetches whatever an
 * event makes stale. The browser's own EventSource retry stops for good after
 * an HTTP error, so this closes the source on any error and reconnects itself.
 */
export function useLiveUpdates(): ConnectionState {
  const client = useQueryClient();
  const [state, setState] = useState<ConnectionState>("connecting");

  useEffect(() => {
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let stopped = false;

    const refreshPatient = (event: StreamEvent) => {
      void client.invalidateQueries({ queryKey: ["census"] });
      void client.invalidateQueries({ queryKey: event.mrn ? ["vitals", event.mrn] : ["vitals"] });
    };

    const connect = () => {
      source = new EventSource(STREAM_URL);
      source.onopen = () => {
        attempt = 0;
        setState("open");
      };
      source.onerror = () => {
        source?.close();
        if (stopped) return;
        setState("reconnecting");
        timer = setTimeout(connect, backoffMs(attempt));
        attempt += 1;
      };
      source.addEventListener("vitals", (message) => refreshPatient(parse(message)));
      source.addEventListener("score", (message) => refreshPatient(parse(message)));
      source.addEventListener("alert", (message) => {
        refreshPatient(parse(message));
        // An action in flight refetches the list itself when it settles, and a
        // refetch now would briefly replace its optimistic change.
        if (client.isMutating() === 0) void client.invalidateQueries({ queryKey: ["alerts"] });
      });
    };

    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      source?.close();
    };
  }, [client]);

  return state;
}
