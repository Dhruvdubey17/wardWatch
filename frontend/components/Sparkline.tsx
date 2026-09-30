const WIDTH = 120;
const HEIGHT = 28;

/** A small NEWS2 trend line. The label carries the values for screen readers. */
export function Sparkline({ values, label }: { values: number[]; label: string }) {
  if (values.length < 2) {
    return <span className="text-xs text-slate-500">Not enough scored hours for a trend</span>;
  }
  const top = Math.max(...values, 7);
  const step = WIDTH / (values.length - 1);
  const points = values
    .map(
      (value, index) =>
        `${(index * step).toFixed(1)},${(HEIGHT - (value / top) * HEIGHT).toFixed(1)}`,
    )
    .join(" ");
  return (
    <svg
      role="img"
      aria-label={`${label}: ${values.join(", ")}`}
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      width={WIDTH}
      height={HEIGHT}
      className="overflow-visible text-slate-700"
    >
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth={1.5} />
    </svg>
  );
}
