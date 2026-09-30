"use client";

import { useQuery } from "@tanstack/react-query";
import { getCensus } from "@/lib/api/client";
import { BedTile } from "./BedTile";
import { QueryState } from "./QueryState";

export function WardBoard() {
  const census = useQuery({ queryKey: ["census"], queryFn: getCensus });
  return (
    <QueryState query={census} what="the ward census">
      {(beds) =>
        beds.length === 0 ? (
          <p className="text-slate-600">No patients are admitted.</p>
        ) : (
          <ul className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {beds.map((bed) => (
              <li key={bed.encounter_id}>
                <BedTile bed={bed} />
              </li>
            ))}
          </ul>
        )
      }
    </QueryState>
  );
}
