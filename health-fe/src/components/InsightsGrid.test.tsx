import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import InsightsGrid from "./InsightsGrid";
import type { InsightStats } from "../api/types";

let container: HTMLDivElement;
let root: Root;

const STATS: InsightStats = {
  hours: 24,
  count: 288,
  in_range_pct: 59,
  mean: 6.53,
  median: 6.84,
  std_dev: 1.62,
  cv: 24.6,
  q1: 5.5,
  q3: 7.8,
  highs: 4,
  lows: 2,
  unicorns: 3,
  normal: { low: 1, in: 95, high: 4 },
  strict: { low: 1, in: 59, high: 40 },
  sparkline: [],
};

function render(stats: InsightStats | null = STATS) {
  act(() =>
    root.render(
      <InsightsGrid
        stats24h={stats}
        gmi={null}
        refreshedAt={0}
      />
    )
  );
}

const card = (title: string) =>
  [...container.querySelectorAll<HTMLElement>(".insight-card")].find(
    (c) => c.querySelector(".insight-card__title")?.textContent === title
  )!;
const big = (title: string) => card(title).querySelector(".insight-value__big")?.textContent;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("InsightsGrid values from /insightstats", () => {
  it("shows each stat on its card", () => {
    render();
    expect(card("% In Range").querySelector(".ring-text")?.textContent).toBe("59");
    expect(big("Average Glucose")).toBe("6.5");
    expect(big("Unicorns")).toBe("3");
    expect(big("Highs / Lows")).toBe("4 / 2");
    expect(big("Median")).toBe("6.8");
    expect(big("Std. Dev.")).toBe("±1.6");
    expect(big("CV")).toBe("25");
    expect(big("Flux")).toBe("A"); // CV 24.6 ≤ 25
    const bar = (title: string) =>
      [...card(title).querySelectorAll(".range-bar span")].map((s) => s.textContent);
    expect(bar("Normal Range %")).toEqual(["1%", "95%", "4%"]);
    expect(bar("Strict Range %")).toEqual(["1%", "59%", "40%"]);
    const labels = [...card("Quartiles").querySelectorAll(".quartile-curve__label")].map((t) => t.textContent);
    expect(labels).toEqual(["5.5", "6.8", "7.8"]);
  });

  it("shows Loading… on stats-based cards until the first 24h stats arrive", () => {
    render(null);
    for (const title of ["% In Range", "Average Glucose", "Unicorns", "Median", "Quartiles", "Mini Graph", "Normal Range %"]) {
      expect(card(title).querySelector(".insight-card__status")?.textContent, title).toBe("Loading…");
    }
  });
});

describe("InsightsGrid card descriptions", () => {
  it("links every card title to a non-empty tooltip", () => {
    render();
    const titles = [...container.querySelectorAll<HTMLElement>(".insight-card__title")];
    expect(titles).toHaveLength(13);
    for (const title of titles) {
      const id = title.getAttribute("aria-describedby");
      const tooltip = id ? document.getElementById(id) : null;
      expect(tooltip?.getAttribute("role"), title.textContent!).toBe("tooltip");
      expect(tooltip?.textContent?.trim(), title.textContent!).not.toBe("");
      // Focusable, so keyboard and touch users can open it too.
      expect(title.tabIndex, title.textContent!).toBe(0);
    }
  });
});
