import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { api } from "./api/client";
import { clearFetchCache } from "./api/useCachedFetch";

vi.mock("./api/client", () => ({
  api: {
    getLastReading: vi.fn(),
    getInsightStats: vi.fn(),
    getGmi: vi.fn(),
    getDailyAvg: vi.fn(),
    getDailyTir: vi.fn(),
  },
}));

// The chart fetches its own data; here we only need the refreshedAt it's
// given, which is what makes it refetch.
const chartRefreshes = new Set<number>();
vi.mock("./components/GlucoseInsulinChart", () => ({
  default: ({ refreshedAt }: { refreshedAt: number }) => {
    chartRefreshes.add(refreshedAt);
    return null;
  },
}));

const mocked = vi.mocked(api);
let container: HTMLDivElement;
let root: Root;
let bgTime: number; // bg_time /lastreading reports; bump it for a new reading
let hidden: boolean;

const calls = () => ({
  lastreading: mocked.getLastReading.mock.calls.length,
  stats: mocked.getInsightStats.mock.calls.length,
  slow: mocked.getGmi.mock.calls.length,
});

async function mount() {
  await act(async () => root.render(<App />));
}

async function minutes(n: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(n * 60_000);
  });
}

async function setHidden(value: boolean) {
  hidden = value;
  await act(async () => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-01T12:00:00Z"));
  vi.clearAllMocks();
  clearFetchCache();
  chartRefreshes.clear();
  bgTime = Date.now() / 1000;
  hidden = false;
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });

  mocked.getLastReading.mockImplementation(async () => ({
    bg_time: bgTime,
    bg_mmol: 6,
    bg_trend: 4,
    bg_mmol_diff: 0,
  }));
  mocked.getInsightStats.mockResolvedValue({
    hours: 24, count: 1, in_range_pct: 100, mean: 6, median: 6, std_dev: 0, cv: 0,
    q1: 6, q3: 6, highs: 0, lows: 0, unicorns: 0,
    normal: { low: 0, in: 100, high: 0 }, strict: { low: 0, in: 100, high: 0 },
    sparkline: [],
  });
  mocked.getGmi.mockResolvedValue({ gmi_percent: 6.6 } as never);
  mocked.getDailyAvg.mockResolvedValue([]);
  mocked.getDailyTir.mockResolvedValue([]);

  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("App polling", () => {
  it("loads everything on start", async () => {
    await mount();
    expect(calls()).toEqual({ lastreading: 1, stats: 1, slow: 1 });
    expect(mocked.getInsightStats).toHaveBeenCalledWith(24);
    expect(mocked.getDailyAvg).toHaveBeenCalledTimes(1);
    expect(mocked.getDailyTir).toHaveBeenCalledTimes(1);
  });

  it("only checks /lastreading while there's no new reading", async () => {
    await mount();
    const refreshes = chartRefreshes.size;
    await minutes(3);
    expect(calls()).toEqual({ lastreading: 4, stats: 1, slow: 1 });
    expect(chartRefreshes.size).toBe(refreshes);
  });

  it("refreshes stats and the chart when a new reading arrives", async () => {
    await mount();
    const refreshes = chartRefreshes.size;
    bgTime += 300;
    await minutes(1);
    expect(calls().stats).toBe(2);
    expect(chartRefreshes.size).toBe(refreshes + 1);
    // The slow data waits for its own 5-minute window.
    expect(calls().slow).toBe(1);
  });

  it("refreshes slow data at most every 5 minutes, even with a reading every minute", async () => {
    await mount();
    for (let i = 0; i < 4; i++) {
      bgTime += 60;
      await minutes(1);
    }
    expect(calls()).toEqual({ lastreading: 5, stats: 5, slow: 1 });
    bgTime += 60;
    await minutes(1);
    expect(calls().slow).toBe(2);
  });

  it("still refreshes every 5 minutes with no new reading (e.g. CGM offline)", async () => {
    await mount();
    await minutes(4);
    expect(calls().stats).toBe(1);
    await minutes(1);
    expect(calls()).toEqual({ lastreading: 6, stats: 2, slow: 2 });
  });

  it("stops polling while hidden and checks at once when shown again", async () => {
    await mount();
    await setHidden(true);
    await minutes(10);
    expect(calls().lastreading).toBe(1);

    bgTime += 600; // readings arrived while hidden
    await setHidden(false);
    expect(calls().lastreading).toBe(2);
    expect(calls().stats).toBe(2);
    // And polling resumes on its usual schedule.
    await minutes(1);
    expect(calls().lastreading).toBe(3);
  });

  it("doesn't start a second poll loop if shown twice", async () => {
    await mount();
    await setHidden(false);
    await minutes(1);
    expect(calls().lastreading).toBe(2);
  });
});
