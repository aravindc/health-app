import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import InsightsGrid from "./InsightsGrid";
import type { DataPoint } from "../api/types";

let container: HTMLDivElement;
let root: Root;

function point(mmol: number, i: number): DataPoint {
  const epoch = Date.parse("2026-09-28T12:00:00.000Z") + i * 300_000;
  return {
    epoch,
    mmol,
    datetime: new Date(epoch).toISOString(),
    date: "2026-09-28",
    time: "12:00",
    in_range: mmol >= 4.0 && mmol <= 7.0,
    point_color: "",
  };
}

function render(mmols: number[]) {
  act(() =>
    root.render(
      <InsightsGrid
        avgMmol24h={null}
        quartiles={[]}
        gmi={null}
        percentInRange={[]}
        sparkline24h={[]}
        dataPoints24h={mmols.map(point)}
      />
    )
  );
}

// The big number on the "Unicorns" card.
function unicornCount(): string | null | undefined {
  const card = [...container.querySelectorAll(".insight-card")].find(
    (c) => c.querySelector(".insight-card__title")?.textContent === "Unicorns"
  );
  return card?.querySelector(".insight-value__big")?.textContent;
}

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

describe("InsightsGrid unicorns", () => {
  it("counts only readings of exactly 5.5 mmol/L", () => {
    render([5.5, 4.0, 5.4, 5.5, 5.6, 7.0, 5.51, 5.5, 12.3]);
    expect(unicornCount()).toBe("3");
  });

  it("is zero when in-range readings never hit 5.5", () => {
    render([4.0, 4.5, 5.0, 6.0, 6.5, 7.0]);
    expect(unicornCount()).toBe("0");
  });

  it("is zero with no readings", () => {
    render([]);
    expect(unicornCount()).toBe("0");
  });

  it("counts 5.5 derived from mg/dL despite floating-point noise", () => {
    // 99 mg/dL / 18 = 5.5; simulate an unrounded conversion off by an ulp.
    render([99 / 18, 5.5 + Number.EPSILON * 4, 5.499999999]);
    expect(unicornCount()).toBe("3");
  });
});
