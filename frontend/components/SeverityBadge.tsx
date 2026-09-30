import type { News2 } from "@/lib/api/client";
import { news2Severity, SEVERITY_STYLE } from "@/lib/severity";

export function SeverityBadge({ news2 }: { news2: News2 | null | undefined }) {
  const style = SEVERITY_STYLE[news2Severity(news2)];
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-sm font-semibold ${style.className}`}
    >
      <span aria-hidden="true">{style.symbol}</span>
      <span>NEWS2 {news2 ? news2.total : "--"}</span>
      <span className="sr-only">, {style.label} risk</span>
      <span aria-hidden="true" className="font-normal">
        {style.label}
      </span>
    </span>
  );
}
