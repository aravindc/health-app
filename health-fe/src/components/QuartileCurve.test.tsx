import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import QuartileCurve from "./QuartileCurve";

let container: HTMLDivElement;
let root: Root;

function render(props: { q1: number; median: number; q3: number; empty?: boolean }) {
  act(() => root.render(<QuartileCurve empty={false} {...props} />));
}

const labels = () =>
  [...container.querySelectorAll(".quartile-curve__label")].map((t) => t.textContent);
const markers = () =>
  [...container.querySelectorAll<SVGLineElement>(".quartile-curve__marker")].map((l) => ({
    x: Number(l.getAttribute("x1")),
    base: Number(l.getAttribute("y1")),
    top: Number(l.getAttribute("y2")),
  }));

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

describe("QuartileCurve", () => {
  it("labels Q1, median and Q3 to one decimal place", () => {
    render({ q1: 5.46, median: 6.8, q3: 7.84 });
    expect(labels()).toEqual(["5.5", "6.8", "7.8"]);
    expect(container.querySelector("svg")?.getAttribute("aria-label")).toBe(
      "Quartiles: Q1 5.5, median 6.8, Q3 7.8 mmol/L"
    );
  });

  it("shows -- with no data", () => {
    render({ q1: 0, median: 0, q3: 0, empty: true });
    expect(labels()).toEqual(["--", "--", "--"]);
    expect(container.querySelector("svg")?.getAttribute("aria-label")).toBe("Quartiles: no data");
  });

  it("follows sin²(πx/4): markers at half height, peak, half height", () => {
    render({ q1: 5, median: 6, q3: 7 });
    const [q1, med, q3] = markers();
    const height = (m: { base: number; top: number }) => m.base - m.top;
    // sin²(π/4) = 0.5 and sin²(π/2) = 1, so the outer markers reach half
    // the median's height, and sit symmetrically either side of it.
    expect(height(q1) / height(med)).toBeCloseTo(0.5, 5);
    expect(height(q3) / height(med)).toBeCloseTo(0.5, 5);
    expect(med.x - q1.x).toBeCloseTo(q3.x - med.x, 5);
  });

  it("starts and ends the curve on the baseline", () => {
    render({ q1: 5, median: 6, q3: 7 });
    const d = container.querySelector(".quartile-curve__line")!.getAttribute("d")!;
    const points = d.replace(/^M /, "").split(" L ").map((p) => p.split(",").map(Number));
    const base = markers()[0].base;
    expect(points[0][1]).toBeCloseTo(base, 1);
    expect(points[points.length - 1][1]).toBeCloseTo(base, 1);
  });

  const gaps = () => {
    const [q1, med, q3] = markers();
    return { below: med.x - q1.x, above: q3.x - med.x };
  };

  it("draws an even spread symmetrically", () => {
    render({ q1: 5, median: 6, q3: 7 });
    const { below, above } = gaps();
    expect(below).toBeCloseTo(above, 5);
  });

  it("widens the side with the larger gap", () => {
    // median − Q1 = 1, Q3 − median = 1.5: right side 1.5× as wide (a
    // 40/60 split, inside the cap).
    render({ q1: 5, median: 6, q3: 7.5 });
    const { below, above } = gaps();
    expect(above / below).toBeCloseTo(1.5, 5);
    // Still half a sin² hump each side: Q1/Q3 at half the peak height.
    const [q1, med, q3] = markers();
    expect((q1.base - q1.top) / (med.base - med.top)).toBeCloseTo(0.5, 5);
    expect((q3.base - q3.top) / (med.base - med.top)).toBeCloseTo(0.5, 5);
  });

  it("caps a very lopsided spread at 65/35", () => {
    render({ q1: 5.9, median: 6, q3: 9 });
    const { below, above } = gaps();
    expect(above / below).toBeCloseTo(0.65 / 0.35, 5);
  });

  it.each([
    [{ q1: 6, median: 6, q3: 6, empty: false }],
    [{ q1: 0, median: 0, q3: 0, empty: true }],
  ])("draws symmetrically when there's no spread: %o", (props) => {
    render(props);
    const { below, above } = gaps();
    expect(below).toBeCloseTo(above, 5);
  });
});
