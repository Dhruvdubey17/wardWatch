import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { AlertInbox } from "@/components/AlertInbox";
import { ALLOWED_ACTIONS } from "@/hooks/useAlertAction";
import type { AlertView } from "@/lib/api/client";
import { alertView } from "./support/fixtures";
import { renderWithClient } from "./support/render";
import { server } from "./support/server";

interface Posted {
  action: string;
  ifMatch: string | null;
  body: Record<string, unknown>;
}

/** Serves one alert list and records every action posted. */
function serveInbox(
  initial: AlertView[],
  respond: (posted: Posted) => Promise<Response> | Response,
) {
  let alerts = initial;
  const posted: Posted[] = [];
  server.use(
    http.get("*/api/alerts", () => HttpResponse.json(alerts)),
    http.post("*/api/alerts/:id/:action", async ({ request, params }) => {
      const entry = {
        action: String(params.action),
        ifMatch: request.headers.get("if-match"),
        body: (await request.json()) as Record<string, unknown>,
      };
      posted.push(entry);
      const response = await respond(entry);
      if (response.ok) {
        const updated = (await response.clone().json()) as AlertView;
        alerts = alerts.map((alert) => (alert.id === updated.id ? updated : alert));
      }
      return response;
    }),
  );
  return {
    posted,
    setAlerts: (next: AlertView[]) => {
      alerts = next;
    },
  };
}

async function panel() {
  return screen.findByRole("region", { name: /alert for MRN-001/ });
}

beforeEach(() => window.localStorage.clear());

test("the allowed actions follow the service's transitions", () => {
  expect(ALLOWED_ACTIONS).toEqual({
    open: ["acknowledge", "escalate"],
    acknowledged: ["escalate", "resolve"],
    escalated: ["resolve"],
    resolved: [],
  });
});

test("acknowledge shows at once, sends If-Match and a note, and keeps the new ETag", async () => {
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => (release = resolve));
  const inbox = serveInbox([alertView()], async ({ body }) => {
    await gate;
    return HttpResponse.json(
      alertView({ status: "acknowledged", updated_by: String(body.actor), etag: '"2"' }),
    );
  });
  renderWithClient(<AlertInbox />);
  const region = await panel();
  await userEvent.type(within(region).getByLabelText("Your name"), "rn.lee");
  await userEvent.type(within(region).getByLabelText("Note (optional)"), "seen, repeating obs");
  await userEvent.click(within(region).getByRole("button", { name: "Acknowledge" }));

  expect(within(region).getByText(/Acknowledged · open for/)).toBeInTheDocument();
  expect(within(region).getByText(/last changed by rn.lee/)).toBeInTheDocument();
  expect(within(region).getByRole("button", { name: "Resolve" })).toBeDisabled();
  release();
  expect(await within(region).findByText("Alert acknowledged by rn.lee.")).toBeInTheDocument();
  expect(inbox.posted).toEqual([
    {
      action: "acknowledge",
      ifMatch: '"1"',
      body: { actor: "rn.lee", note: "seen, repeating obs" },
    },
  ]);
  expect(window.localStorage.getItem("wardwatch.actor")).toBe("rn.lee");
});

test("escalate needs a reason and sends it", async () => {
  const inbox = serveInbox([alertView({ status: "acknowledged", etag: '"4"' })], ({ body }) =>
    HttpResponse.json(
      alertView({ status: "escalated", updated_by: String(body.actor), etag: '"5"' }),
    ),
  );
  window.localStorage.setItem("wardwatch.actor", "dr.kim");
  renderWithClient(<AlertInbox />);
  const region = await panel();
  expect(within(region).getByLabelText("Your name")).toHaveValue("dr.kim");
  await userEvent.click(within(region).getByRole("button", { name: "Escalate" }));
  expect(within(region).getByRole("alert")).toHaveTextContent("Choose a reason for escalating.");
  expect(inbox.posted).toEqual([]);

  await userEvent.selectOptions(
    within(region).getByLabelText("Escalation reason"),
    "Sepsis pathway started",
  );
  await userEvent.click(within(region).getByRole("button", { name: "Escalate" }));
  expect(await within(region).findByText("Alert escalated by dr.kim.")).toBeInTheDocument();
  expect(inbox.posted).toEqual([
    {
      action: "escalate",
      ifMatch: '"4"',
      body: { actor: "dr.kim", note: null, reason: "sepsis_pathway_started" },
    },
  ]);
  expect(within(region).getByRole("button", { name: "Resolve" })).toBeInTheDocument();
  expect(within(region).queryByRole("button", { name: "Escalate" })).not.toBeInTheDocument();
});

test("resolve from escalated", async () => {
  const inbox = serveInbox([alertView({ status: "escalated" })], () =>
    HttpResponse.json(alertView({ status: "resolved", updated_by: "rn.lee", etag: '"2"' })),
  );
  renderWithClient(<AlertInbox />);
  const region = await panel();
  expect(
    within(region)
      .getAllByRole("button")
      .map((button) => button.textContent),
  ).toEqual(["Resolve"]);
  await userEvent.type(within(region).getByLabelText("Your name"), "rn.lee");
  await userEvent.click(within(region).getByRole("button", { name: "Resolve" }));
  expect(await within(region).findByText(/Resolved · open for/)).toBeInTheDocument();
  expect(inbox.posted.map((entry) => entry.action)).toEqual(["resolve"]);
});

test("a name is required", async () => {
  const inbox = serveInbox([alertView()], () => HttpResponse.json(alertView()));
  renderWithClient(<AlertInbox />);
  const region = await panel();
  await userEvent.type(within(region).getByLabelText("Your name"), "   ");
  await userEvent.click(within(region).getByRole("button", { name: "Acknowledge" }));
  expect(within(region).getByRole("alert")).toHaveTextContent("Enter your name");
  expect(inbox.posted).toEqual([]);
});

test("a server error rolls the change back", async () => {
  serveInbox([alertView()], async () => {
    await delay(20);
    return HttpResponse.json({ detail: "database unavailable" }, { status: 503 });
  });
  renderWithClient(<AlertInbox />);
  const region = await panel();
  await userEvent.type(within(region).getByLabelText("Your name"), "rn.lee");
  await userEvent.click(within(region).getByRole("button", { name: "Acknowledge" }));
  expect(within(region).getByText(/Acknowledged · open for/)).toBeInTheDocument();
  expect(await within(region).findByRole("alert")).toHaveTextContent(
    "Not saved: database unavailable. Your change was undone.",
  );
  expect(within(region).getByText(/ICU hour 14 · Open · open for/)).toBeInTheDocument();
  expect(within(region).queryByText(/last changed by/)).not.toBeInTheDocument();
});

test("an unreachable service rolls the change back", async () => {
  serveInbox([alertView()], () => HttpResponse.error());
  renderWithClient(<AlertInbox />);
  const region = await panel();
  await userEvent.type(within(region).getByLabelText("Your name"), "rn.lee");
  await userEvent.click(within(region).getByRole("button", { name: "Acknowledge" }));
  expect(await within(region).findByRole("alert")).toHaveTextContent(/unreachable.*undone/);
  expect(within(region).getByText(/· Open · open for/)).toBeInTheDocument();
});

test.each([
  [412, "the alert changed since you loaded it; dr.kim set it to acknowledged"],
  [409, "cannot acknowledge an alert that is acknowledged"],
])("a %i shows who changed the alert and refreshes it", async (status, detail) => {
  const current = alertView({ status: "acknowledged", updated_by: "dr.kim", etag: '"2"' });
  const inbox = serveInbox([alertView()], () => {
    inbox.setAlerts([current]);
    return HttpResponse.json({ detail, alert: current }, { status });
  });
  renderWithClient(<AlertInbox />);
  const region = await panel();
  await userEvent.type(within(region).getByLabelText("Your name"), "rn.lee");
  await userEvent.click(within(region).getByRole("button", { name: "Acknowledge" }));
  expect(await within(region).findByRole("alert")).toHaveTextContent(
    `Not saved: ${detail}. The alert has been refreshed.`,
  );
  expect(
    within(region).getByText(/Acknowledged · open for .* · last changed by dr.kim/),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(within(region).getByRole("button", { name: "Resolve" })).toBeEnabled(),
  );
  await userEvent.click(within(region).getByRole("button", { name: "Resolve" }));
  await waitFor(() => expect(inbox.posted.at(-1)?.ifMatch).toBe('"2"'));
});
