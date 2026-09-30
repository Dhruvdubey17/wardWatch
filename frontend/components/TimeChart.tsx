"use client";

import { CartesianGrid, Line, LineChart, ReferenceLine, Tooltip, XAxis, YAxis } from "recharts";
import { formatClock, formatNumber } from "@/lib/format";

export interface TimePoint {
  at: number;
  value: number | null;
}

/**
 * A line over clock time with a dashed marker for each alert. The chart is
 * decoration for sighted users; the table under it carries the same values.
 */
export function TimeChart({
  title,
  unit,
  points,
  markers,
  step = false,
  digits = 0,
}: {
  title: string;
  unit: string | null;
  points: TimePoint[];
  markers: number[];
  step?: boolean;
  digits?: number;
}) {
  const heading = unit ? `${title} (${unit})` : title;
  return (
    <figure className="rounded-lg border border-slate-200 bg-white p-4">
      <figcaption className="mb-2 font-medium">{heading}</figcaption>
      <div aria-hidden="true">
        {/* Hidden from assistive technology, so it must not take keyboard focus either. */}
        <LineChart
          responsive
          accessibilityLayer={false}
          data={points}
          style={{ width: "100%", height: 180 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis
            dataKey="at"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(at: number) => formatClock(new Date(at).toISOString())}
          />
          <YAxis width={40} domain={["auto", "auto"]} />
          <Tooltip labelFormatter={(at) => formatClock(new Date(Number(at)).toISOString())} />
          {markers.map((at) => (
            <ReferenceLine key={at} x={at} stroke="#b91c1c" strokeDasharray="4 2" />
          ))}
          <Line
            type={step ? "stepAfter" : "monotone"}
            dataKey="value"
            stroke="#0f172a"
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
        </LineChart>
      </div>
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-slate-600">Show values</summary>
        <table className="mt-2 w-full text-left">
          <caption className="sr-only">{heading}</caption>
          <thead>
            <tr>
              <th scope="col">Time (UTC)</th>
              <th scope="col">Value</th>
            </tr>
          </thead>
          <tbody>
            {points.map((point) => (
              <tr key={point.at}>
                <td>{formatClock(new Date(point.at).toISOString())}</td>
                <td>{formatNumber(point.value, digits)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
