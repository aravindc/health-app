import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import PredictedGlucose from "./PredictedGlucose";
import type { Forecast } from "../chart/forecast";
import { COLOR_CGM_ELEVATED, COLOR_CGM_IN_RANGE } from "../chart/cgmColors";

let container: HTMLDivElement;
let root: Root;

const point = (minutes: number, value: number, readingAgoMin = 2) => ({
  time: Date.now() - readingAgoMin * 60_000 + minutes * 60_000,
  minutes,
  value,
  lower: value - 1,
  upper: value + 1,
  lower95: value - 2,
  upper95: value + 2,
});

const forecast = (readingAgoMin = 2): Forecast => ({
  points: [point(0, 6.2, readingAgoMin), point(15, 6.84, readingAgoMin), point(30, 7.46, readingAgoMin)],
  warnings: [],
  disclaimer: "Research use only.",
});

function render(f: Forecast | null) {
  act(() => root.render(<PredictedGlucose forecast={f} />));
}

const block = () => container.querySelector(".predicted-glucose");
const rows = () =>
  [...container.querySelectorAll(".predicted-glucose__row")].map((r) => r.textContent);

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

describe("PredictedGlucose", () => {
  it("shows the 15- and 30-minute predictions to one decimal", () => {
    render(forecast());
    expect(block()!.querySelector(".predicted-glucose__title")!.textContent).toBe("Predicted");
    expect(rows()).toEqual(["6.8 in 15 mins", "7.5 in 30 mins"]);
  });

  it("colours each value by its range, like the dial", () => {
    render(forecast());
    const colors = [...container.querySelectorAll<HTMLElement>(".predicted-glucose__value")].map((v) =>
      v.style.getPropertyValue("--predicted-color")
    );
    expect(colors).toEqual([COLOR_CGM_IN_RANGE, COLOR_CGM_ELEVATED]);
  });

  it("gives the 80% ranges and the disclaimer for hover and screen readers", () => {
    render(forecast());
    expect(block()!.getAttribute("title")).toContain("Research use only.");
    expect(container.querySelector(".predicted-glucose__row")!.getAttribute("aria-label")).toBe(
      "6.8 mmol/L in 15 minutes, 80% range 5.8 to 7.8"
    );
  });

  it("is hidden without a forecast", () => {
    render(null);
    expect(block()).toBeNull();
  });
});
