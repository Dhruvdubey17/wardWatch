import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { AlertInbox } from "@/components/AlertInbox";
import { alertView, censusBed, NOW } from "./support/fixtures";
import { renderWithClient } from "./support/render";
import { server } from "./support/server";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});
afterEach(() => {
  vi.useRealTimers();
});

const high = alertView({
  id: "a-high",
  mrn: "MRN-HIGH",
  news2: { ...alertView().news2, total: 9 },
});
const news2Alert = alertView({
  id: "a-news2",
  mrn: "MRN-N2",
  source: "news2",
  calibrated_probability: null,
  top_factors: [],
  model_version: "news2-rcp-2017",
  news2: { ...alertView().news2, total: 5, single_parameter_three: false },
  raised_at: "2026-09-30T09:45:00Z",
  status: "acknowledged",
  updated_by: "rn.lee",
});

test("requests unresolved alerts and lists them most severe first", async () => {
  let statuses = "";
  server.use(
    http.get("*/api/alerts", ({ request }) => {
      statuses = new URL(request.url).searchParams.get("status") ?? "";
      return HttpResponse.json([news2Alert, high]);
    }),
  );
  renderWithClient(<AlertInbox />);
  const list = await screen.findByRole("list", { name: "Unresolved alerts, most severe first" });
  expect(statuses).toBe("open,acknowledged,escalated");
  const rows = within(list).getAllByRole("button");
  expect(rows.map((row) => row.textContent)).toEqual([
    "▲ HighMRN-HIGHSepsis model · 42% · Open · open 1 h",
    "◆ MediumMRN-N2NEWS2 · -- · Acknowledged · open 2 h 15 min",
  ]);
  expect(rows[0]).toHaveAttribute("aria-pressed", "true");
});

test("the explanation panel shows factors and the NEWS2 breakdown", async () => {
  server.use(http.get("*/api/alerts", () => HttpResponse.json([high])));
  renderWithClient(<AlertInbox />);
  const panel = await screen.findByRole("region", { name: "Sepsis model alert for MRN-HIGH" });
  expect(within(panel).getByText(/ICU hour 14 · Open · open for 1 h/)).toBeInTheDocument();
  expect(within(panel).getByText("42%")).toBeInTheDocument();
  expect(within(panel).getByRole("listitem")).toHaveTextContent(
    "Respiratory rate up 8/min over 6 h",
  );
  const breakdown = within(panel).getByRole("table", { name: "NEWS2 breakdown, total 9" });
  expect(within(breakdown).getByRole("row", { name: "Respiratory rate 3" })).toHaveClass(
    "font-semibold",
  );
  expect(within(breakdown).getByRole("row", { name: "Consciousness 0" })).not.toHaveClass(
    "font-semibold",
  );
});

test("selecting another alert changes the panel", async () => {
  server.use(http.get("*/api/alerts", () => HttpResponse.json([high, news2Alert])));
  renderWithClient(<AlertInbox />);
  await userEvent.click(await screen.findByRole("button", { name: /MRN-N2/ }));
  const panel = screen.getByRole("region", { name: "NEWS2 alert for MRN-N2" });
  expect(within(panel).getByText(/last changed by rn.lee/)).toBeInTheDocument();
  expect(within(panel).getByText(/no model factors/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /MRN-N2/ })).toHaveAttribute("aria-pressed", "true");
});

test("an empty inbox", async () => {
  server.use(http.get("*/api/alerts", () => HttpResponse.json([])));
  renderWithClient(<AlertInbox />);
  expect(await screen.findByText("No unresolved alerts.")).toBeInTheDocument();
});

test("rows name the patient and bed when the patient is on the census", async () => {
  server.use(
    http.get("*/api/alerts", () => HttpResponse.json([alertView(), news2Alert])),
    http.get("*/api/ward/census", () => HttpResponse.json([censusBed()])),
  );
  renderWithClient(<AlertInbox />);
  expect(await screen.findByRole("button", { name: /Ada Okafor, bed ICU-01/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /MRN-N2/ })).toBeInTheDocument();
  const panel = screen.getByRole("region", {
    name: "Sepsis model alert for Ada Okafor, bed ICU-01",
  });
  expect(within(panel).getByText(/MRN-001 · ICU hour 14/)).toBeInTheDocument();
});
