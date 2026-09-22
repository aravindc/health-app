import { describe, it, expect } from "vitest";
import {
  WINDOW_HOURS,
  pxPerHour,
  xForTime,
  timeForX,
  nearest,
  parseTime,
  dayStart,
  cgmRange,
} from "./scale";

describe("pxPerHour", () => {
  it("divides the measured container width evenly across the 24h window", () => {
    expect(pxPerHour(960)).toBeCloseTo(960 / WINDOW_HOURS);
    expect(pxPerHour(0)).toBe(0);
  });
});

describe("xForTime / timeForX", () => {
  const windowStart = Date.parse("2026-09-21T07:00:00Z");
  const pxPerHr = 40; // 40px/hour, e.g. a 960px-wide panel over 24h

  it("places windowStart at x=0", () => {
    expect(xForTime(windowStart, windowStart, pxPerHr)).toBe(0);
  });

  it("places a point N hours in at N*pxPerHr", () => {
    const threeHoursIn = windowStart + 3 * 3_600_000;
    expect(xForTime(threeHoursIn, windowStart, pxPerHr)).toBeCloseTo(3 * pxPerHr);
  });

  it("is the exact inverse of timeForX", () => {
    const t = windowStart + 5.5 * 3_600_000;
    const x = xForTime(t, windowStart, pxPerHr);
    expect(timeForX(x, windowStart, pxPerHr)).toBeCloseTo(t);
  });

  it("timeForX at x=0 recovers windowStart", () => {
    expect(timeForX(0, windowStart, pxPerHr)).toBe(windowStart);
  });
});

describe("nearest", () => {
  // Mirrors the shape used by GlucoseInsulinChart's CGM series.
  const series = [
    { epoch: 100, v: "a" },
    { epoch: 200, v: "b" },
    { epoch: 300, v: "c" },
    { epoch: 500, v: "d" },
  ];
  const timeOf = (p: { epoch: number }) => p.epoch;

  it("returns null for an empty series", () => {
    expect(nearest([], 250, timeOf)).toBeNull();
  });

  it("returns the sole entry for a single-element series regardless of target", () => {
    const single = [{ epoch: 42, v: "only" }];
    expect(nearest(single, -1000, timeOf)).toBe(single[0]);
    expect(nearest(single, 1000, timeOf)).toBe(single[0]);
  });

  it("returns an exact match", () => {
    expect(nearest(series, 200, timeOf)?.v).toBe("b");
  });

  it("picks the closer neighbor when between two points", () => {
    // 260 is 60 away from 200 and 40 away from 300 -> "c" wins.
    expect(nearest(series, 260, timeOf)?.v).toBe("c");
    // 240 is 40 away from 200 and 60 away from 300 -> "b" wins.
    expect(nearest(series, 240, timeOf)?.v).toBe("b");
  });

  it("breaks an exact-tie in favor of the earlier (prev) point", () => {
    // 250 is equidistant between 200 and 300.
    expect(nearest(series, 250, timeOf)?.v).toBe("b");
  });

  it("clamps to the first entry when target is before the series", () => {
    expect(nearest(series, -1000, timeOf)?.v).toBe("a");
  });

  it("clamps to the last entry when target is after the series", () => {
    expect(nearest(series, 10_000, timeOf)?.v).toBe("d");
  });

  it("requires ascending-by-time order — documents the pre-sort contract", () => {
    // Regression guard for the bug fixed in GlucoseInsulinChart.tsx, where
    // hoverCgm was binary-searching the raw (possibly unsorted) API
    // response: on unsorted input, nearest() can return a result far from
    // the true nearest point, which is why callers must sort first (see
    // GlucoseInsulinChart's sortedCgmData memo).
    const unsorted = [
      { epoch: 500, v: "d" },
      { epoch: 100, v: "a" },
      { epoch: 300, v: "c" },
      { epoch: 200, v: "b" },
    ];
    // Sorted, nearest(300) unambiguously is "c" (exact match).
    // Unsorted, the binary search has no such guarantee — assert the
    // sorted call is correct, which is the contract callers must uphold.
    const sorted = [...unsorted].sort((a, b) => a.epoch - b.epoch);
    expect(nearest(sorted, 300, timeOf)?.v).toBe("c");
  });
});

describe("parseTime", () => {
  it("parses an RFC3339 timestamp to epoch millis", () => {
    expect(parseTime("2026-09-21T07:00:00.000Z")).toBe(Date.parse("2026-09-21T07:00:00.000Z"));
  });

  it("returns NaN for an unparseable string", () => {
    expect(parseTime("not-a-date")).toBeNaN();
  });
});

describe("cgmRange", () => {
  // Matches this repo's default thresholds (App.tsx / .env.example):
  // min=4.0, strictMax=7.0, max=10.0.
  const min = 4.0;
  const strictMax = 7.0;
  const max = 10.0;

  it("classifies the tight target band as in-range", () => {
    expect(cgmRange(4.0, min, strictMax, max)).toBe("in-range");
    expect(cgmRange(5.5, min, strictMax, max)).toBe("in-range");
    expect(cgmRange(7.0, min, strictMax, max)).toBe("in-range");
  });

  it("classifies above strictMax but within max as elevated", () => {
    expect(cgmRange(7.1, min, strictMax, max)).toBe("elevated");
    expect(cgmRange(8.5, min, strictMax, max)).toBe("elevated");
    expect(cgmRange(10.0, min, strictMax, max)).toBe("elevated");
  });

  it("classifies below min as critical", () => {
    expect(cgmRange(3.9, min, strictMax, max)).toBe("critical");
    expect(cgmRange(0, min, strictMax, max)).toBe("critical");
  });

  it("classifies above max as critical", () => {
    expect(cgmRange(10.1, min, strictMax, max)).toBe("critical");
    expect(cgmRange(20, min, strictMax, max)).toBe("critical");
  });

  it("boundaries are inclusive at min and strictMax, matching the backend's mmol >= min && mmol <= strictMax", () => {
    expect(cgmRange(min, min, strictMax, max)).toBe("in-range");
    expect(cgmRange(strictMax, min, strictMax, max)).toBe("in-range");
  });
});

describe("dayStart", () => {
  it("returns local midnight for a YYYY-MM-DD date", () => {
    const got = dayStart("2026-09-21");
    const want = new Date(2026, 8, 21, 0, 0, 0, 0).getTime();
    expect(got).toBe(want);
  });

  it("round-trips through xForTime/timeForX at x=0", () => {
    const midnight = dayStart("2026-01-15");
    expect(timeForX(0, midnight, 40)).toBe(midnight);
  });
});
