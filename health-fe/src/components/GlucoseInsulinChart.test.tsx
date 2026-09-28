import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import GlucoseInsulinChart from "./GlucoseInsulinChart";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: {
    getFirstDate: vi.fn(),
    getRangeChart: vi.fn(),
    getBolusRangeChart: vi.fn(),
    getBasalRangeChart: vi.fn(),
  },
}));

const HOUR = 3_600_000;
const WINDOW_MS = 24 * HOUR;
const T0 = Date.parse("2026-09-28T12:00:00.000Z");

// jsdom has no ResizeObserver; the panels only need it to measure width.
class ResizeObserverStub {
  observe() {}
  disconnect() {}
}

let container: HTMLDivElement;
let root: Root;

function render(refreshedAt: number) {
  act(() => root.render(<GlucoseInsulinChart refreshedAt={refreshedAt} />));
}

// Let the debounced refetch fire and its promises settle.
async function flushRefetch() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(300);
  });
}

// [from, to] of the most recent /chart request, as epoch ms.
function lastChartWindow(): [number, number] {
  const calls = vi.mocked(api.getRangeChart).mock.calls;
  const [from, to] = calls[calls.length - 1];
  return [Date.parse(from), Date.parse(to)];
}

function clickButton(title: string) {
  const button = container.querySelector<HTMLButtonElement>(`button[title="${title}"]`);
  if (!button) throw new Error(`no button titled ${title}`);
  act(() => button.click());
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  vi.useFakeTimers();
  vi.setSystemTime(T0);

  vi.mocked(api.getFirstDate).mockResolvedValue({ date: "2020-01-01" });
  vi.mocked(api.getRangeChart).mockResolvedValue([]);
  vi.mocked(api.getBolusRangeChart).mockResolvedValue({
    doses: [],
    food_activity: [],
    correction_activity: [],
  } as unknown as Awaited<ReturnType<typeof api.getBolusRangeChart>>);
  vi.mocked(api.getBasalRangeChart).mockResolvedValue({
    points: [],
    window_end: new Date(T0).toISOString(),
  } as unknown as Awaited<ReturnType<typeof api.getBasalRangeChart>>);

  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("GlucoseInsulinChart refresh", () => {
  it("moves the window forward on each refresh while following the latest data", async () => {
    render(T0);
    await flushRefetch();
    expect(lastChartWindow()).toEqual([T0 - WINDOW_MS, T0]);

    // A minute later the parent refreshes: the window must end at the new
    // refresh time, so readings that arrived since are included.
    const T1 = T0 + 60_000;
    vi.setSystemTime(T1);
    render(T1);
    await flushRefetch();
    expect(lastChartWindow()).toEqual([T1 - WINDOW_MS, T1]);
  });

  it("keeps a panned-back window pinned across refreshes until Latest", async () => {
    render(T0);
    await flushRefetch();

    clickButton("Back 24h");
    await flushRefetch();
    const pinned: [number, number] = [T0 - 2 * WINDOW_MS, T0 - WINDOW_MS];
    expect(lastChartWindow()).toEqual(pinned);

    // A refresh refetches the pinned window rather than jumping to now.
    const T1 = T0 + 60_000;
    vi.setSystemTime(T1);
    const callsBefore = vi.mocked(api.getRangeChart).mock.calls.length;
    render(T1);
    await flushRefetch();
    expect(vi.mocked(api.getRangeChart).mock.calls.length).toBeGreaterThan(callsBefore);
    expect(lastChartWindow()).toEqual(pinned);

    // "Latest" resumes following, ending at the latest refresh.
    clickButton("Latest (now)");
    await flushRefetch();
    expect(lastChartWindow()).toEqual([T1 - WINDOW_MS, T1]);
  });

  it("resumes following after jumping forward back to now", async () => {
    render(T0);
    await flushRefetch();

    clickButton("Back 24h");
    await flushRefetch();
    clickButton("Forward 24h");
    await flushRefetch();

    const T1 = T0 + 60_000;
    vi.setSystemTime(T1);
    render(T1);
    await flushRefetch();
    expect(lastChartWindow()).toEqual([T1 - WINDOW_MS, T1]);
  });
});
