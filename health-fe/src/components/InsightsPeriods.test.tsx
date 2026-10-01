import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import InsightsGrid from "./InsightsGrid";
import { api } from "../api/client";
import { clearFetchCache } from "../api/useCachedFetch";
import type { InsightStats } from "../api/types";

vi.mock("../api/client", () => ({
  api: {
    getInsightStats: vi.fn(),
    getGmi: vi.fn(),
  },
}));

const mocked = vi.mocked(api);
let container: HTMLDivElement;
let root: Root;

const stats = (over: Partial<InsightStats>): InsightStats => ({
  hours: 24, count: 3, in_range_pct: 67, mean: 6.5, median: 6, std_dev: 1, cv: 16, q1: 5, q3: 7,
  highs: 0, lows: 0, unicorns: 0,
  normal: { low: 0, in: 100, high: 0 },
  strict: { low: 0, in: 100, high: 0 },
  sparkline: [],
  ...over,
});

function render() {
  act(() =>
    root.render(
      <InsightsGrid
        stats24h={stats({ median: 6 })}
        gmi={{ gmi_percent: 6.6 } as never}
        refreshedAt={0}
      />
    )
  );
}

const cardNamed = (title: string) =>
  [...container.querySelectorAll<HTMLElement>(".insight-card")].find(
    (c) => c.querySelector(".insight-card__title")?.textContent === title
  )!;
const bigValue = (title: string) =>
  cardNamed(title).querySelector(".insight-value__big")?.textContent;
const periodLabel = (title: string) =>
  cardNamed(title).querySelector(".insight-card__period")?.textContent;
const dialog = () => document.querySelector<HTMLElement>('[role="dialog"]');
const option = (label: string) =>
  [...dialog()!.querySelectorAll<HTMLButtonElement>(".period-dialog__option")].find(
    (b) => b.textContent === label
  )!;

async function choose(title: string, label: string) {
  act(() => cardNamed(title).click());
  await act(async () => option(label).click());
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  clearFetchCache();
  vi.clearAllMocks();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("Insight card periods", () => {
  it("opens a dialog with the four periods, current one selected", () => {
    render();
    act(() => cardNamed("Median").click());
    const labels = [...dialog()!.querySelectorAll(".period-dialog__option")].map((b) => b.textContent);
    expect(labels).toEqual(["24H", "7d", "14d", "90d"]);
    expect(option("24H").getAttribute("aria-pressed")).toBe("true");
    expect(document.activeElement).toBe(option("24H"));
  });

  it("switches a stats-based card to 7 days and updates its value, label and tooltip", async () => {
    let resolve!: (v: InsightStats) => void;
    mocked.getInsightStats.mockReturnValue(new Promise((r) => (resolve = r)));
    render();
    expect(bigValue("Median")).toBe("6.0");

    await choose("Median", "7d");
    expect(dialog()).toBeNull();
    expect(mocked.getInsightStats).toHaveBeenCalledWith(168);
    expect(periodLabel("Median")).toBe("7 days");
    expect(cardNamed("Median").querySelector(".insight-card__status")?.textContent).toBe("Loading…");

    await act(async () => resolve(stats({ hours: 168, median: 9 })));
    expect(bigValue("Median")).toBe("9.0");
    expect(cardNamed("Median").querySelector('[role="tooltip"]')?.textContent).toContain("last 7 days");
    // Other cards keep their own period.
    expect(periodLabel("Std. Dev.")).toBe("24 hours");
  });

  it("shares one /insightstats fetch between cards on the same period", async () => {
    mocked.getInsightStats.mockResolvedValue(stats({ hours: 2160, highs: 1, lows: 0 }));
    render();
    await choose("Median", "90d");
    await choose("Highs / Lows", "90d");
    await choose("Quartiles", "90d");
    expect(mocked.getInsightStats).toHaveBeenCalledTimes(1);
    expect(mocked.getInsightStats).toHaveBeenCalledWith(2160);
    expect(bigValue("Highs / Lows")).toBe("1 / 0");
  });

  it.each([
    ["Average Glucose", "14d", () => mocked.getInsightStats, 336, stats({ hours: 336 })],
    ["% In Range", "7d", () => mocked.getInsightStats, 168, stats({ hours: 168 })],
    ["Quartiles", "14d", () => mocked.getInsightStats, 336, stats({ hours: 336 })],
  ] as const)("fetches %s for %s from its own endpoint", async (title, label, fn, arg, response) => {
    fn().mockResolvedValue(response as never);
    render();
    await choose(title, label);
    expect(fn()).toHaveBeenCalledWith(arg);
  });

  it("shows Average Glucose for the chosen period", async () => {
    mocked.getInsightStats.mockResolvedValue(stats({ hours: 2160, mean: 7.25 }));
    render();
    await choose("Average Glucose", "90d");
    expect(bigValue("Average Glucose")).toBe("7.3");
    expect(periodLabel("Average Glucose")).toBe("90 days");
  });

  it("disables GMI periods of 7 days or fewer and explains why", async () => {
    mocked.getGmi.mockResolvedValue({ gmi_percent: 7.1 } as never);
    render();
    expect(periodLabel("GMI")).toBe("90 days");
    act(() => cardNamed("GMI").click());
    expect(option("24H").disabled).toBe(true);
    expect(option("7d").disabled).toBe(true);
    expect(option("14d").disabled).toBe(false);
    expect(dialog()!.textContent).toContain("GMI needs more than 7 days of readings.");
    await act(async () => option("14d").click());
    expect(mocked.getGmi).toHaveBeenCalledWith(14);
    expect(bigValue("GMI")).toBe("7.1");
  });

  it("closes on Escape and returns focus to the period button", () => {
    render();
    act(() => cardNamed("CV").querySelector<HTMLButtonElement>(".insight-card__period")!.click());
    expect(dialog()).not.toBeNull();
    act(() => {
      dialog()!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(dialog()).toBeNull();
    expect(document.activeElement).toBe(cardNamed("CV").querySelector(".insight-card__period"));
  });

  it("closes on a backdrop click without changing the period", () => {
    render();
    act(() => cardNamed("CV").click());
    act(() => document.querySelector<HTMLElement>(".period-dialog__backdrop")!.click());
    expect(dialog()).toBeNull();
    expect(periodLabel("CV")).toBe("24 hours");
  });

  it("says when a period's data couldn't be loaded", async () => {
    mocked.getInsightStats.mockRejectedValue(new Error("500 Internal Server Error"));
    render();
    await choose("Unicorns", "14d");
    const status = cardNamed("Unicorns").querySelector(".insight-card__status");
    expect(status?.textContent).toBe("Couldn't load");
    expect(status?.getAttribute("title")).toBe("500 Internal Server Error");
  });
});
