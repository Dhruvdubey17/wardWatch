import axe from "axe-core";

/**
 * Runs axe over a rendered tree. jsdom does no layout, so the color-contrast
 * rule cannot compute colors here and is left to the browser tests.
 */
export async function axeViolations(container: Element): Promise<string[]> {
  const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
  return results.violations.map(
    (violation) => `${violation.id}: ${violation.nodes.map((node) => node.html).join(" | ")}`,
  );
}
