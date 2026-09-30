import { alertSeverity, sortAlerts } from "@/lib/alerts";
import { alertView } from "./support/fixtures";

function withNews2(total: number, single = false) {
  return { ...alertView().news2, total, single_parameter_three: single };
}

test("a NEWS2 alert takes its NEWS2 band", () => {
  expect(alertSeverity(alertView({ source: "news2", news2: withNews2(8) }))).toBe("high");
  expect(alertSeverity(alertView({ source: "news2", news2: withNews2(3, true) }))).toBe(
    "low-medium",
  );
});

test("a model alert ranks at least medium", () => {
  expect(alertSeverity(alertView({ source: "model", news2: withNews2(1) }))).toBe("medium");
  expect(alertSeverity(alertView({ source: "model", news2: withNews2(9) }))).toBe("high");
});

test("sorted by severity, then oldest first, then by ID", () => {
  const alerts = [
    alertView({ id: "medium-new", news2: withNews2(5), raised_at: "2026-09-30T11:30:00Z" }),
    alertView({ id: "high-new", news2: withNews2(9), raised_at: "2026-09-30T11:50:00Z" }),
    alertView({ id: "medium-old", news2: withNews2(6), raised_at: "2026-09-30T09:00:00Z" }),
    alertView({ id: "high-old", news2: withNews2(7), raised_at: "2026-09-30T10:00:00Z" }),
    alertView({
      id: "news2-low",
      source: "news2",
      news2: withNews2(3, true),
      raised_at: "2026-09-30T08:00:00Z",
    }),
    alertView({ id: "high-old-b", news2: withNews2(7), raised_at: "2026-09-30T10:00:00Z" }),
  ];
  expect(sortAlerts(alerts).map((alert) => alert.id)).toEqual([
    "high-old",
    "high-old-b",
    "high-new",
    "medium-old",
    "medium-new",
    "news2-low",
  ]);
  expect(alerts[0]!.id).toBe("medium-new");
});
