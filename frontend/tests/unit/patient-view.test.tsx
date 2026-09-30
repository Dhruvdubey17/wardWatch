import { screen, within } from "@testing-library/react";
import { http, HttpResponse, type JsonBodyType } from "msw";
import { alertTimes, PatientView } from "@/components/PatientView";
import { patientVitals } from "./support/fixtures";
import { renderWithClient } from "./support/render";
import { server } from "./support/server";

function serve(body: JsonBodyType, status = 200) {
  server.use(http.get("*/api/patients/:mrn/vitals", () => HttpResponse.json(body, { status })));
}

test("alerts are drawn at the end of their ICU hour", () => {
  expect(alertTimes(patientVitals())).toEqual([Date.parse("2026-09-30T12:00:00Z")]);
  expect(alertTimes(patientVitals({ admitted_at: null }))).toEqual([]);
});

test("shows the patient, alerts, NEWS2 timeline and vitals", async () => {
  serve(patientVitals());
  renderWithClient(<PatientView mrn="MRN-001" />);
  expect(await screen.findByRole("heading", { level: 1, name: "Ada Okafor" })).toBeInTheDocument();
  expect(screen.getByText(/MRN-001 · Bed ICU-01 · Admitted 22:00 UTC/)).toBeInTheDocument();
  expect(screen.getByText("NEWS2 7")).toBeInTheDocument();
  expect(screen.getByText("ICU hour 14: Model alert, open")).toBeInTheDocument();

  const news2 = screen.getByRole("table", { name: "NEWS2 total" });
  expect(
    within(news2)
      .getAllByRole("row")
      .slice(1)
      .map((row) => row.textContent),
  ).toEqual(["13:005", "14:007"]);
  const heart = screen.getByRole("table", { name: "Heart rate (/min)" });
  expect(within(heart).getAllByRole("row")).toHaveLength(3);
  expect(screen.getByRole("heading", { name: "Vitals, last 24 hours" })).toBeInTheDocument();
});

test("a patient with nothing scored or observed yet", async () => {
  serve(
    patientVitals({ scores: [], series: {}, alerts: [], name: null, bed: null, admitted_at: null }),
  );
  renderWithClient(<PatientView mrn="MRN-001" />);
  expect(await screen.findByRole("heading", { level: 1, name: "MRN-001" })).toBeInTheDocument();
  expect(screen.getByText("No alerts.")).toBeInTheDocument();
  expect(screen.getByText("No hours have been scored yet.")).toBeInTheDocument();
  expect(screen.getByText("No observations in this window.")).toBeInTheDocument();
});

test("a patient without an encounter", async () => {
  serve({ mrn: "MRN-009", encounter_id: null, series: {}, scores: [], alerts: [] });
  renderWithClient(<PatientView mrn="MRN-009" />);
  expect(await screen.findByText("This patient has no encounter on record.")).toBeInTheDocument();
});

test("an unknown patient", async () => {
  serve({ detail: "no patient with MRN MRN-404" }, 404);
  renderWithClient(<PatientView mrn="MRN-404" />);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Could not load patient MRN-404: no patient with MRN MRN-404",
  );
});
