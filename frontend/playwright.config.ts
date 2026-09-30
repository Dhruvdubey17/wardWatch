import { defineConfig, devices } from "@playwright/test";

// The end to end suite runs against the compose stack (`make up`), which
// serves the dashboard on 127.0.0.1:3000 unless WARDWATCH_E2E_BASE_URL says otherwise.
export default defineConfig({
  testDir: "tests/e2e",
  timeout: 120_000,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.WARDWATCH_E2E_BASE_URL ?? "http://127.0.0.1:3000",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
