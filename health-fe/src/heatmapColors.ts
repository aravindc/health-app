// Color functions for the Daily Average / Time-In-Range heatmaps
// (Heatmap.tsx). Pulled out of that component file, rather than living
// alongside it, because Heatmap.tsx exports only the component itself —
// mixing plain function exports into a component file breaks Vite Fast
// Refresh (react-refresh/only-export-components). See chart/scale.ts for
// the same pattern applied to the chart panels' pure math.

/** Color for a Daily Average heatmap cell. */
export function avgColor(mmol: number, strict: boolean): string {
  const low = 4.0;
  const high = strict ? 7.0 : 10.0;

  if (mmol < low) return "#c0392b"; // red — low
  if (mmol <= high) return "#27ae60"; // green — in range
  if (mmol <= high * 1.1) return "#e67e22"; // orange — slightly above
  if (mmol <= high * 1.2) return "#d35400"; // dark orange — above
  return "#c0392b"; // red — high
}

/**
 * Color for a Time-In-Range heatmap cell. Matches Heatmap's shared
 * `colorFn` signature (value, strict) => string so it's interchangeable
 * with avgColor as a prop, but TIR color bands don't depend on strict
 * vs. medical range — the thresholds are the same either way.
 */
export function tirColor(pir: number): string {
  if (pir >= 85) return "#196f3d"; // dark green
  if (pir >= 70) return "#27ae60"; // green
  if (pir >= 55) return "#f1c40f"; // yellow
  if (pir >= 40) return "#e67e22"; // orange
  if (pir >= 25) return "#d35400"; // dark orange
  return "#c0392b"; // red
}
