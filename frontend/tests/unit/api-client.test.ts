import { http, HttpResponse } from "msw";
import { actOnAlert, ApiError, getAlert, getCensus, getVitals, listAlerts } from "@/lib/api/client";
import { alertView, censusBed, patientVitals } from "./support/fixtures";
import { server } from "./support/server";

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  const error = await promise.then(
    () => null,
    (reason: unknown) => reason,
  );
  expect(error).toBeInstanceOf(ApiError);
  return error as ApiError;
}

test("reads the census", async () => {
  server.use(http.get("*/api/ward/census", () => HttpResponse.json([censusBed()])));
  expect(await getCensus()).toEqual([censusBed()]);
});

test("sends the status filter as a comma-separated list", async () => {
  let query = "";
  server.use(
    http.get("*/api/alerts", ({ request }) => {
      query = new URL(request.url).searchParams.get("status") ?? "";
      return HttpResponse.json([alertView()]);
    }),
  );
  await listAlerts(["open", "escalated"]);
  expect(query).toBe("open,escalated");
});

test("omits the status filter when none is given", async () => {
  let url = "";
  server.use(
    http.get("*/api/alerts", ({ request }) => {
      url = request.url;
      return HttpResponse.json([]);
    }),
  );
  await listAlerts();
  expect(new URL(url).search).toBe("");
});

test("reads vitals with and without a window", async () => {
  const hours: (string | null)[] = [];
  server.use(
    http.get("*/api/patients/:mrn/vitals", ({ request, params }) => {
      expect(params.mrn).toBe("MRN-001");
      hours.push(new URL(request.url).searchParams.get("hours"));
      return HttpResponse.json(patientVitals());
    }),
  );
  await getVitals("MRN-001", 12);
  await getVitals("MRN-001");
  expect(hours).toEqual(["12", null]);
});

test("a 404 carries the service's detail", async () => {
  server.use(
    http.get("*/api/alerts/:id", () =>
      HttpResponse.json({ detail: "alert nope is not known" }, { status: 404 }),
    ),
  );
  const error = await failure(getAlert("nope"));
  expect(error.status).toBe(404);
  expect(error.message).toBe("alert nope is not known");
  expect(error.isConflict).toBe(false);
  expect(error.current).toBeNull();
});

test("a validation error joins the messages", async () => {
  server.use(
    http.get("*/api/patients/:mrn/vitals", () =>
      HttpResponse.json(
        { detail: [{ msg: "hours must be at least 1" }, { loc: ["query"] }] },
        { status: 422 },
      ),
    ),
  );
  const error = await failure(getVitals("MRN-001", 0));
  expect(error.message).toBe("hours must be at least 1; invalid request");
});

test("an error without a body gets a generic message", async () => {
  server.use(http.get("*/api/ward/census", () => new HttpResponse("oops", { status: 502 })));
  const error = await failure(getCensus());
  expect(error.status).toBe(502);
  expect(error.message).toBe("request failed with status 502");
});

test("a network failure has status 0", async () => {
  server.use(http.get("*/api/ward/census", () => HttpResponse.error()));
  const error = await failure(getCensus());
  expect(error.status).toBe(0);
  expect(error.message).toMatch(/unreachable/);
});

test("actions send If-Match and the body", async () => {
  const seen: { path: string; ifMatch: string | null; body: unknown }[] = [];
  server.use(
    http.post("*/api/alerts/:id/:action", async ({ request, params }) => {
      seen.push({
        path: `${String(params.id)}/${String(params.action)}`,
        ifMatch: request.headers.get("if-match"),
        body: await request.json(),
      });
      return HttpResponse.json(alertView({ status: "acknowledged", etag: '"2"' }));
    }),
  );
  const alert = alertView();
  const updated = await actOnAlert(alert, {
    kind: "acknowledge",
    input: { actor: "rn.lee", note: "" },
  });
  await actOnAlert(alert, {
    kind: "escalate",
    input: { actor: "rn.lee", note: "rising RR", reason: "senior_review_needed" },
  });
  await actOnAlert(alert, { kind: "resolve", input: { actor: "rn.lee" } });
  expect(updated.etag).toBe('"2"');
  expect(seen).toEqual([
    { path: "a-1/acknowledge", ifMatch: '"1"', body: { actor: "rn.lee", note: null } },
    {
      path: "a-1/escalate",
      ifMatch: '"1"',
      body: { actor: "rn.lee", note: "rising RR", reason: "senior_review_needed" },
    },
    { path: "a-1/resolve", ifMatch: '"1"', body: { actor: "rn.lee", note: null } },
  ]);
});

test.each([409, 412])("a %i carries the alert as the server holds it", async (status) => {
  const current = alertView({ status: "acknowledged", updated_by: "dr.kim", etag: '"2"' });
  server.use(
    http.post("*/api/alerts/:id/acknowledge", () =>
      HttpResponse.json({ detail: "the alert changed", alert: current }, { status }),
    ),
  );
  const error = await failure(
    actOnAlert(alertView(), { kind: "acknowledge", input: { actor: "rn.lee" } }),
  );
  expect(error.isConflict).toBe(true);
  expect(error.current).toEqual(current);
});
