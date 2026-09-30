import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { AlertInbox } from "@/components/AlertInbox";
import { ConnectionIndicator } from "@/components/ConnectionIndicator";
import { PatientView } from "@/components/PatientView";
import { WardBoard } from "@/components/WardBoard";
import type { AlertView } from "@/lib/api/client";
import { axeViolations } from "./support/axe";
import { FakeEventSource } from "./support/fake-event-source";
import { alertView, censusBed, patientVitals } from "./support/fixtures";
import { renderWithClient } from "./support/render";
import { server } from "./support/server";

const second = alertView({
  id: "a-2",
  mrn: "MRN-002",
  news2: { ...alertView().news2, total: 5, single_parameter_three: false },
});
const third = alertView({
  id: "a-3",
  mrn: "MRN-003",
  source: "news2",
  news2: { ...alertView().news2, total: 3, single_parameter_three: true },
});

function serveAlerts(alerts: AlertView[]) {
  const posted: string[] = [];
  server.use(
    http.get("*/api/alerts", () => HttpResponse.json(alerts)),
    http.post("*/api/alerts/:id/:action", ({ params }) => {
      posted.push(`${String(params.id)}/${String(params.action)}`);
      return HttpResponse.json(
        alertView({ id: String(params.id), status: "acknowledged", etag: '"2"' }),
      );
    }),
  );
  return posted;
}

beforeEach(() => window.localStorage.clear());

test("the ward board has no axe violations", async () => {
  server.use(http.get("*/api/ward/census", () => HttpResponse.json([censusBed()])));
  const { container } = renderWithClient(<WardBoard />);
  await screen.findByRole("article");
  expect(await axeViolations(container)).toEqual([]);
});

test("the patient view has no axe violations", async () => {
  server.use(http.get("*/api/patients/:mrn/vitals", () => HttpResponse.json(patientVitals())));
  const { container } = renderWithClient(<PatientView mrn="MRN-001" />);
  await screen.findByRole("heading", { level: 1 });
  expect(await axeViolations(container)).toEqual([]);
});

test("the inbox and workflow have no axe violations", async () => {
  serveAlerts([alertView(), second]);
  const { container } = renderWithClient(<AlertInbox />);
  await screen.findByRole("region", { name: /alert for MRN-001/ });
  expect(await axeViolations(container)).toEqual([]);
});

test("the error and empty states have no axe violations", async () => {
  server.use(
    http.get("*/api/ward/census", () => HttpResponse.json({ detail: "down" }, { status: 503 })),
  );
  const { container } = renderWithClient(<WardBoard />);
  await screen.findByRole("alert");
  expect(await axeViolations(container)).toEqual([]);
});

test("the connection indicator has no axe violations", async () => {
  vi.stubGlobal("EventSource", FakeEventSource);
  const { container } = renderWithClient(<ConnectionIndicator />);
  expect(await axeViolations(container)).toEqual([]);
  vi.unstubAllGlobals();
});

test("arrow keys, Home and End move through the inbox", async () => {
  serveAlerts([alertView(), second, third]);
  renderWithClient(<AlertInbox />);
  const list = await screen.findByRole("list", { name: /Unresolved alerts/ });
  const rows = within(list).getAllByRole("button");
  await userEvent.tab();
  expect(rows[0]).toHaveFocus();

  await userEvent.keyboard("{ArrowDown}");
  expect(rows[1]).toHaveFocus();
  expect(rows[1]).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("region", { name: /alert for MRN-002/ })).toBeInTheDocument();

  await userEvent.keyboard("{End}");
  expect(rows[2]).toHaveFocus();
  await userEvent.keyboard("{ArrowDown}");
  expect(rows[2]).toHaveFocus();
  await userEvent.keyboard("{Home}");
  expect(rows[0]).toHaveFocus();
  await userEvent.keyboard("{ArrowUp}");
  expect(rows[0]).toHaveFocus();
  await userEvent.keyboard("{ArrowRight}");
  expect(rows[0]).toHaveFocus();
  expect(rows.map((row) => row.tabIndex)).toEqual([0, -1, -1]);
});

test("an alert can be acknowledged with the keyboard alone", async () => {
  const posted = serveAlerts([alertView(), second]);
  renderWithClient(<AlertInbox />);
  await screen.findByRole("list", { name: /Unresolved alerts/ });

  await userEvent.tab();
  await userEvent.keyboard("{ArrowDown}");
  const region = screen.getByRole("region", { name: /alert for MRN-002/ });
  // The list is one tab stop, so Tab goes straight on to the workflow form.
  await userEvent.tab();
  expect(within(region).getByLabelText("Your name")).toHaveFocus();
  await userEvent.keyboard("rn.lee");
  await userEvent.tab();
  expect(within(region).getByLabelText("Note (optional)")).toHaveFocus();
  await userEvent.tab();
  expect(within(region).getByRole("button", { name: "Acknowledge" })).toHaveFocus();
  await userEvent.keyboard("{Enter}");
  expect(await within(region).findByText(/Alert acknowledged by rn.lee/)).toBeInTheDocument();
  expect(posted).toEqual(["a-2/acknowledge"]);
});

test("escalating with the keyboard picks a reason from the list", async () => {
  const posted = serveAlerts([alertView()]);
  renderWithClient(<AlertInbox />);
  const region = await screen.findByRole("region", { name: /alert for MRN-001/ });
  await userEvent.type(within(region).getByLabelText("Your name"), "rn.lee");
  const reason = within(region).getByLabelText("Escalation reason");
  reason.focus();
  await userEvent.selectOptions(reason, "clinical_deterioration");
  await userEvent.tab();
  expect(within(region).getByRole("button", { name: "Escalate" })).toHaveFocus();
  await userEvent.keyboard("{Enter}");
  await within(region).findByRole("status");
  expect(posted).toEqual(["a-1/escalate"]);
});
