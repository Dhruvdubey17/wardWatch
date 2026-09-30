import { expect, test } from "@playwright/test";
import type { ChildProcess } from "node:child_process";
import { axeViolations, startSimulator } from "./support";

// Needs a fresh stack: `make up` or scripts/local_stack.sh start.
test.describe.configure({ mode: "serial" });

let simulator: ChildProcess;

test.beforeAll(() => {
  simulator = startSimulator();
});

test.afterAll(() => {
  simulator.kill();
});

test("a deteriorating patient's alert is acknowledged, escalated and stays so after a reload", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByRole("status").filter({ hasText: /^Live$/ })).toBeVisible();
  // The board fills in over the event stream, without a reload.
  const deteriorating = page.getByRole("article").filter({ hasText: /unresolved alert/ });
  await expect(deteriorating.first()).toBeVisible({ timeout: 90_000 });
  await expect(deteriorating.first()).toContainText(/NEWS2 \d+/);

  await page
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: "Alerts" })
    .click();
  const rows = page.getByRole("list", { name: /Unresolved alerts/ }).getByRole("button");
  await expect(rows.first()).toBeVisible();
  const alertId = await rows.first().getAttribute("data-alert-id");
  expect(alertId).toBeTruthy();
  const row = page.locator(`button[data-alert-id="${alertId}"]`);
  await row.click();

  const panel = page.getByRole("region", { name: /alert for/ });
  await panel.getByLabel("Your name").fill("e2e.nurse");
  await panel.getByLabel("Note (optional)").fill("Reviewed at the bedside");
  await panel.getByRole("button", { name: "Acknowledge" }).click();
  await expect(panel.getByRole("status")).toHaveText("Alert acknowledged by e2e.nurse.");

  await panel.getByLabel("Escalation reason").selectOption("senior_review_needed");
  await panel.getByRole("button", { name: "Escalate" }).click();
  await expect(panel.getByRole("status")).toHaveText("Alert escalated by e2e.nurse.");

  await page.reload();
  await page.locator(`button[data-alert-id="${alertId}"]`).click();
  const reloaded = page.getByRole("region", { name: /alert for/ });
  await expect(reloaded).toContainText("Escalated");
  await expect(reloaded).toContainText("last changed by e2e.nurse");
  await expect(reloaded.getByRole("button", { name: "Resolve" })).toBeVisible();

  const stored = await page.request.get(`/api/alerts/${alertId}`);
  expect(stored.ok()).toBe(true);
  expect(await stored.json()).toMatchObject({ status: "escalated", updated_by: "e2e.nurse" });
});

test("the ward, patient and inbox pages pass axe, color contrast included", async ({ page }) => {
  await page.goto("/");
  const tile = page.getByRole("article").first();
  await expect(tile).toBeVisible({ timeout: 90_000 });
  expect(await axeViolations(page)).toEqual([]);

  await tile.getByRole("link").click();
  await expect(page.getByRole("heading", { name: "NEWS2 timeline" })).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);

  await page.goto("/alerts");
  await expect(page.getByRole("region", { name: /alert for/ })).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);
});
