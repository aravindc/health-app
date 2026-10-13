import { describe, expect, it } from "vitest";
import type { Prediction } from "../api/types";
import { forecastAt, toForecast } from "./forecast";

const iv = (value: number) => ({
  value,
  lower: value - 1,
  upper: value + 1,
  lower_95: value - 2,
  upper_95: value + 2,
  confidence_level: 0.8,
});

const available: Prediction = {
  available: true,
  timestamp: "2026-10-13T08:36:00Z",
  current_glucose: 8,
  unit: "mmol/L",
  predictions: { "15m": iv(8.5), "30m": iv(9), "45m": iv(9.4), "60m": iv(9.7) },
  model_version: {},
  warnings: ["w"],
  disclaimer: "Research use only.",
};
const T = Date.parse("2026-10-13T08:36:00Z");

describe("toForecast", () => {
  it("starts at the reading with a zero-width interval, then +15 and +30 min", () => {
    const f = toForecast(available)!;
    expect(f.points.map((p) => [p.time - T, p.value, p.lower, p.upper95])).toEqual([
      [0, 8, 8, 8],
      [15 * 60_000, 8.5, 7.5, 10.5],
      [30 * 60_000, 9, 8, 11],
    ]);
    expect(f.warnings).toEqual(["w"]);
    expect(f.disclaimer).toBe("Research use only.");
  });

  it("is null without a usable prediction", () => {
    expect(toForecast(null)).toBeNull();
    expect(toForecast({ available: false, reason: "stale" })).toBeNull();
    expect(toForecast({ ...available, predictions: { "15m": iv(8.5) } })).toBeNull();
  });
});

describe("forecastAt", () => {
  const f = toForecast(available)!;
  it("returns the nearest predicted point inside the forecast", () => {
    expect(forecastAt(f, T + 10 * 60_000)?.minutes).toBe(15);
    expect(forecastAt(f, T + 25 * 60_000)?.minutes).toBe(30);
    expect(forecastAt(f, T + 31 * 60_000)?.minutes).toBe(30);
  });
  it("is null at or before the reading, and past the end", () => {
    expect(forecastAt(f, T)).toBeNull();
    expect(forecastAt(f, T - 60_000)).toBeNull();
    expect(forecastAt(f, T + 40 * 60_000)).toBeNull();
  });
});
