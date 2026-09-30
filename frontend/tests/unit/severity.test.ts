import { news2Severity } from "@/lib/severity";
import { alertView } from "./support/fixtures";

const news2 = alertView().news2;

test.each([
  [0, false, "low"],
  [4, false, "low"],
  [3, true, "low-medium"],
  [5, false, "medium"],
  [6, true, "medium"],
  [7, false, "high"],
  [12, true, "high"],
])("NEWS2 %i (single three: %s) is %s", (total, single, expected) => {
  expect(news2Severity({ ...news2, total, single_parameter_three: single })).toBe(expected);
});

test("a bed without a score is unknown", () => {
  expect(news2Severity(null)).toBe("unknown");
  expect(news2Severity(undefined)).toBe("unknown");
});
