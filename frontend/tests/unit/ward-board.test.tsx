import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { BedTile } from "@/components/BedTile";
import { WardBoard } from "@/components/WardBoard";
import { alertView, censusBed, scorePoint } from "./support/fixtures";
import { renderWithClient } from "./support/render";
import { server } from "./support/server";

test("a tile shows the patient, vitals, NEWS2, risk and trend", () => {
  renderWithClient(<BedTile bed={censusBed()} />);
  const tile = screen.getByRole("article", { name: "Bed ICU-01, Ada Okafor" });
  expect(within(tile).getByRole("link", { name: "Ada Okafor" })).toHaveAttribute(
    "href",
    "/patients/MRN-001",
  );
  expect(within(tile).getByText("MRN-001")).toBeInTheDocument();
  for (const [label, value] of [
    ["HR", "112"],
    ["RR", "26"],
    ["SpO2", "93"],
    ["SBP", "98"],
    ["Temp", "38.4"],
  ] as const) {
    expect(within(tile).getByText(label).nextSibling).toHaveTextContent(value);
  }
  expect(within(tile).getByText("NEWS2 7")).toBeInTheDocument();
  expect(within(tile).getByText(", High risk")).toBeInTheDocument();
  expect(within(tile).getByText("42%")).toBeInTheDocument();
  expect(within(tile).getByText("1 unresolved alert")).toBeInTheDocument();
  expect(within(tile).getByRole("img", { name: "NEWS2, last 12 hours: 3, 5" })).toBeInTheDocument();
});

test("severity has a text cue as well as color", () => {
  const news2 = { ...alertView().news2, total: 5, single_parameter_three: false };
  renderWithClient(<BedTile bed={censusBed({ latest_score: scorePoint(3, { news2 }) })} />);
  expect(screen.getByText("Medium", { selector: "[aria-hidden]" })).toBeInTheDocument();
});

test("a new admission without scores or vitals", () => {
  renderWithClient(
    <BedTile
      bed={censusBed({ latest_score: null, trend: [], vitals: {}, unresolved_alerts: 0 })}
    />,
  );
  expect(screen.getByText("NEWS2 --")).toBeInTheDocument();
  expect(screen.getByText(", Not scored risk")).toBeInTheDocument();
  expect(screen.getByText("Not enough scored hours for a trend")).toBeInTheDocument();
  expect(screen.queryByText(/unresolved/)).not.toBeInTheDocument();
  expect(screen.getAllByText("--").length).toBeGreaterThanOrEqual(6);
});

test("the board lists every bed", async () => {
  server.use(
    http.get("*/api/ward/census", () =>
      HttpResponse.json([
        censusBed(),
        censusBed({
          bed: "ICU-02",
          encounter_id: "E2",
          mrn: "MRN-002",
          name: { family: "Ruiz", given: "Tomas" },
          unresolved_alerts: 2,
        }),
      ]),
    ),
  );
  renderWithClient(<WardBoard />);
  expect(screen.getByRole("status")).toHaveTextContent("Loading the ward census");
  expect(await screen.findAllByRole("article")).toHaveLength(2);
  expect(screen.getByText("2 unresolved alerts")).toBeInTheDocument();
});

test("an empty ward", async () => {
  server.use(http.get("*/api/ward/census", () => HttpResponse.json([])));
  renderWithClient(<WardBoard />);
  expect(await screen.findByText("No patients are admitted.")).toBeInTheDocument();
});

test("a failed load can be retried", async () => {
  let calls = 0;
  server.use(
    http.get("*/api/ward/census", () => {
      calls += 1;
      return calls === 1
        ? HttpResponse.json({ detail: "database unavailable" }, { status: 503 })
        : HttpResponse.json([censusBed()]);
    }),
  );
  renderWithClient(<WardBoard />);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Could not load the ward census: database unavailable",
  );
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("article")).toBeInTheDocument();
});
