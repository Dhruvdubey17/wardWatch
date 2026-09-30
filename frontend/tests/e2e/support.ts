import { spawn, type ChildProcess } from "node:child_process";
import { createRequire } from "node:module";
import type { Page } from "@playwright/test";

const require = createRequire(import.meta.url);

/**
 * Replays the committed fixture stays into the stack's MLLP port. One of them
 * deteriorates from its first hours, so the ward gets a patient with alerts.
 */
export function startSimulator(): ChildProcess {
  const [host = "127.0.0.1", port = "2575"] = (process.env.WARDWATCH_E2E_MLLP ?? "")
    .split(":")
    .filter(Boolean);
  const fixtures = "../python/simulator/tests/fixtures";
  return spawn(
    "uv",
    [
      "run",
      "--project",
      "../python",
      "wardwatch-sim",
      "replay",
      "--physionet-dir",
      `${fixtures}/physionet`,
      "--synthea-dir",
      `${fixtures}/synthea`,
      "--sites",
      "A",
      "--beds",
      "3",
      "--seconds-per-hour",
      "2",
      "--host",
      host,
      "--port",
      port,
    ],
    { stdio: "inherit" },
  );
}

/** axe violations on the page as it stands, color contrast included. */
export async function axeViolations(page: Page): Promise<string[]> {
  await page.addScriptTag({ path: require.resolve("axe-core") });
  return page.evaluate(async () => {
    const results = await (
      window as unknown as {
        axe: { run: () => Promise<{ violations: { id: string; nodes: { html: string }[] }[] }> };
      }
    ).axe.run();
    return results.violations.map(
      (violation) => `${violation.id}: ${violation.nodes.map((node) => node.html).join(" | ")}`,
    );
  });
}
