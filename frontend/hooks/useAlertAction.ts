"use client";

import { useMutation, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { actOnAlert, ApiError, type AlertAction, type AlertView } from "@/lib/api/client";

const NEXT_STATUS = {
  acknowledge: "acknowledged",
  escalate: "escalated",
  resolve: "resolved",
} as const;

/** Which actions the workflow allows from each status, matching the service. */
export const ALLOWED_ACTIONS: Record<string, AlertAction["kind"][]> = {
  open: ["acknowledge", "escalate"],
  acknowledged: ["escalate", "resolve"],
  escalated: ["resolve"],
  resolved: [],
};

function replace(list: AlertView[] | undefined, alert: AlertView): AlertView[] | undefined {
  return list?.map((item) => (item.id === alert.id ? alert : item));
}

/**
 * Applies an action to the alert list under `listKey` straight away, then
 * keeps the server's answer. A failure puts the list back; a 409 or 412 shows
 * the alert as the server holds it now.
 */
export function useAlertAction(listKey: QueryKey) {
  const client = useQueryClient();
  return useMutation<
    AlertView,
    Error,
    { alert: AlertView; action: AlertAction },
    AlertView[] | undefined
  >({
    mutationFn: ({ alert, action }) => actOnAlert(alert, action),
    onMutate: async ({ alert, action }) => {
      await client.cancelQueries({ queryKey: listKey });
      const previous = client.getQueryData<AlertView[]>(listKey);
      client.setQueryData<AlertView[]>(listKey, (list) =>
        replace(list, {
          ...alert,
          status: NEXT_STATUS[action.kind],
          updated_by: action.input.actor,
        }),
      );
      return previous;
    },
    onSuccess: (updated) => {
      client.setQueryData<AlertView[]>(listKey, (list) => replace(list, updated));
    },
    onError: (error, _variables, previous) => {
      client.setQueryData(listKey, previous);
      if (error instanceof ApiError && error.current) {
        const current = error.current;
        client.setQueryData<AlertView[]>(listKey, (list) => replace(list, current));
      }
    },
    onSettled: () => {
      void client.invalidateQueries({ queryKey: listKey });
      void client.invalidateQueries({ queryKey: ["census"] });
    },
  });
}
