import type { UseQueryResult } from "@tanstack/react-query";
import type { ReactNode } from "react";

/** Loading and error states shared by every page that reads from the API. */
export function QueryState<T>({
  query,
  what,
  children,
}: {
  query: UseQueryResult<T>;
  what: string;
  children: (data: T) => ReactNode;
}) {
  if (query.isPending) return <p role="status">Loading {what}…</p>;
  if (query.isError) {
    return (
      <div role="alert" className="rounded border border-red-300 bg-red-50 p-3 text-red-900">
        Could not load {what}: {query.error.message}{" "}
        <button type="button" className="underline" onClick={() => void query.refetch()}>
          Try again
        </button>
      </div>
    );
  }
  return <>{children(query.data)}</>;
}
