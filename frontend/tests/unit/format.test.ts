import {
  formatClock,
  formatDuration,
  formatFactor,
  formatNumber,
  formatProbability,
} from "@/lib/format";

const factor = { feature: "x", contribution: 0.3 };

test("numbers and probabilities", () => {
  expect(formatNumber(112)).toBe("112");
  expect(formatNumber(38.44, 1)).toBe("38.4");
  expect(formatNumber(null)).toBe("--");
  expect(formatNumber(Number.NaN)).toBe("--");
  expect(formatProbability(0.424)).toBe("42%");
  expect(formatProbability(null)).toBe("--");
});

test("durations", () => {
  const now = new Date("2026-09-30T12:00:00Z");
  expect(formatDuration("2026-09-30T11:48:00Z", now)).toBe("12 min");
  expect(formatDuration("2026-09-30T10:00:00Z", now)).toBe("2 h");
  expect(formatDuration("2026-09-30T09:45:00Z", now)).toBe("2 h 15 min");
  expect(formatDuration("2026-09-27T09:00:00Z", now)).toBe("3 d 3 h");
  expect(formatDuration("2026-09-30T12:05:00Z", now)).toBe("0 min");
});

test("clock times are UTC", () => {
  expect(formatClock("2026-09-30T07:05:00Z")).toBe("07:05");
});

test.each([
  ["Respiratory rate, change over 6 h", "/min", 8, "Respiratory rate up 8/min over 6 h"],
  [
    "Systolic blood pressure, change over 6 h",
    "mmHg",
    -22,
    "Systolic blood pressure down 22 mmHg over 6 h",
  ],
  ["Temperature, change over 6 h", "°C", 1.2, "Temperature up 1.2 °C over 6 h"],
  ["Heart rate, change over 6 h", "/min", 0, "Heart rate unchanged over 6 h"],
  ["Lactate, latest", "mmol/L", 3.1, "Lactate 3.1 mmol/L"],
  ["SpO2, latest", "%", 91, "SpO2 91%"],
  ["Heart rate, highest over 6 h", "/min", 131, "Heart rate 131/min (highest over 6 h)"],
  ["Temperature, highest over 6 h", "°C", 38.93, "Temperature 38.9 °C (highest over 6 h)"],
  ["Heart rate, latest", "/min", 131.4, "Heart rate 131/min"],
  ["Respiratory rate, change over 6 h", "/min", 8.4, "Respiratory rate up 8.4/min over 6 h"],
  ["Lactate, measured this hour", "", 1, "Lactate measured this hour"],
  ["Lactate, measured this hour", "", 0, "Lactate not measured this hour"],
  ["Lactate, hours since last measured", "h", 5, "Lactate last measured 5 h ago"],
  ["NEWS2 total", "points", 7, "NEWS2 total 7 points"],
  ["Inspired oxygen fraction, latest", "", 0.4, "Inspired oxygen fraction 0.4"],
])("%s = %d reads as %s", (label, unit, value, expected) => {
  expect(formatFactor({ ...factor, label, unit, value })).toBe(expected);
});

test("a factor without a value", () => {
  expect(formatFactor({ ...factor, label: "Lactate, latest", unit: "mmol/L", value: null })).toBe(
    "Lactate: not measured",
  );
});
