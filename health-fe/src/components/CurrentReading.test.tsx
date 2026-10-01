import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import CurrentReading from "./CurrentReading";
import type { LastReading } from "../api/types";
import {
  COLOR_CGM_CRITICAL,
  COLOR_CGM_ELEVATED,
  COLOR_CGM_IN_RANGE,
} from "../chart/cgmColors";

let container: HTMLDivElement;
let root: Root;

function render(reading: Partial<LastReading> | null) {
  const full = reading && {
    bg_time: Date.now() / 1000,
    bg_mmol: 6.2,
    bg_trend: 4,
    bg_mmol_diff: -0.2,
    ...reading,
  };
  act(() => root.render(<CurrentReading reading={full} />));
}

const el = () => container.querySelector(".current-reading")!;
const pointer = () =>
  container.querySelector<HTMLElement>(".current-reading__pointer");
const chevrons = () =>
  container.querySelectorAll(".current-reading__chevron").length;

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

describe("CurrentReading colour", () => {
  // Same bands and RGB values as the chart's CGM bars.
  it.each([
    [3.9, "critical", COLOR_CGM_CRITICAL],
    [4.0, "in-range", COLOR_CGM_IN_RANGE],
    [7.0, "in-range", COLOR_CGM_IN_RANGE],
    [7.1, "elevated", COLOR_CGM_ELEVATED],
    [10.0, "elevated", COLOR_CGM_ELEVATED],
    [10.1, "critical", COLOR_CGM_CRITICAL],
  ])("%s mmol/L → %s %s", (mmol, range, color) => {
    render({ bg_mmol: mmol });
    expect(el().classList).toContain(`current-reading--${range}`);
    expect((el() as HTMLElement).style.getPropertyValue("--reading-color")).toBe(color);
  });

  it("shows the reading inside the circle", () => {
    render({ bg_mmol: 6.24 });
    expect(container.querySelector(".current-reading__circle .current-reading__value")?.textContent).toBe("6.2");
  });

  it.each([
    [2.17, "LOW", "current-reading--critical"],
    [22.28, "HIGH", "current-reading--critical"],
  ])("%s mmol/L shows %s in place of the number", (mmol, word, cls) => {
    render({ bg_mmol: mmol });
    expect(el().classList).toContain(cls);
    expect(container.querySelector(".current-reading__value")?.textContent).toBe(word);
    expect(container.querySelector('[role="img"]')?.getAttribute("aria-label")).toBe(
      `${word}, steady`
    );
  });

  it.each([2.2, 22.2])("%s mmol/L is still shown as a number", (mmol) => {
    render({ bg_mmol: mmol });
    expect(container.querySelector(".current-reading__value")?.textContent).toBe(mmol.toFixed(1));
  });

  it("shows a grey placeholder with no reading", () => {
    render(null);
    expect(el().classList).toContain("current-reading--none");
    expect(container.querySelector(".current-reading__value")?.textContent).toBe("--");
    expect(pointer()).toBeNull();
  });
});

describe("CurrentReading trend pointer", () => {
  it.each([
    [1, -90, 2, "rising quickly"],
    [2, -90, 1, "rising"],
    [3, -45, 1, "rising slowly"],
    [4, 0, 1, "steady"],
    [5, 45, 1, "falling slowly"],
    [6, 90, 1, "falling"],
    [7, 90, 2, "falling quickly"],
  ])("trend %s → %s° with %s chevron(s)", (trend, angle, count, label) => {
    render({ bg_trend: trend, bg_mmol: 6.2 });
    expect(pointer()?.style.transform).toBe(`rotate(${angle}deg)`);
    expect(chevrons()).toBe(count);
    expect(container.querySelector('[role="img"]')?.getAttribute("aria-label")).toBe(
      `6.2 mmol/L, ${label}`
    );
  });

  it.each([
    [0, "no trend"],
    [8, "trend unavailable"],
    [9, "rate out of range"],
    [99, "trend unavailable"],
  ])("trend %s draws no pointer and reads as %s", (trend, label) => {
    render({ bg_trend: trend, bg_mmol: 6.2 });
    expect(pointer()).toBeNull();
    expect(container.querySelector(".current-reading__value")?.textContent).toBe("6.2");
    expect(container.querySelector('[role="img"]')?.getAttribute("aria-label")).toBe(
      `6.2 mmol/L, ${label}`
    );
  });
});

describe("CurrentReading stale data", () => {
  const minsAgo = (m: number) => Date.now() / 1000 - m * 60;
  const meta = () => container.querySelector(".current-reading__meta")?.textContent;

  it.each([
    [0, "just now"],
    [5, "5 min ago"],
    [14, "14 min ago"],
  ])("%s min old is current: %s", (mins, label) => {
    render({ bg_time: minsAgo(mins) });
    expect(el().classList).not.toContain("current-reading--stale");
    expect(meta()).toBe(label);
  });

  it.each([15, 22, 90])("%s min old is stale and flagged", (mins) => {
    render({ bg_time: minsAgo(mins), bg_mmol: 5.8, bg_trend: 4 });
    expect(el().classList).toContain("current-reading--stale");
    expect(meta()).toBe(`⚠ ${mins} min ago`);
    expect(container.querySelector('[role="img"]')?.getAttribute("aria-label")).toBe(
      `5.8 mmol/L, steady, ${mins} min old`
    );
  });
});

describe("CurrentReading change since last reading", () => {
  const diff = () => container.querySelector(".current-reading__diff");

  it.each([
    [0, "± 0.0", "no change 0.0"],
    [-0, "± 0.0", "no change 0.0"],
    [0.04, "± 0.0", "no change 0.0"],
    [0.4, "▲ 0.4", "up 0.4"],
    [-0.3, "▼ 0.3", "down 0.3"],
    [-1.25, "▼ 1.3", "down 1.3"],
  ])("%s shows %s", (bg_mmol_diff, shown, spoken) => {
    render({ bg_mmol_diff });
    const el = diff()!;
    const mark = el.querySelector('[aria-hidden="true"]')!.textContent;
    const word = el.querySelector(".current-reading__sr")!.textContent;
    const number = el.textContent!.replace(mark!, "").replace(word!, "");
    expect(`${mark}${number}`).toBe(shown);
    expect(`${word}${number}`).toBe(spoken);
  });
});
