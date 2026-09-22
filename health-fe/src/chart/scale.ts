// Shared time-axis math for the CGM / bolus-activity / basal panel family
// (GlucoseInsulinChart.tsx). Each panel is drawn as an SVG sized to
// exactly fill its container's measured width with the full WINDOW_HOURS
// window — no horizontal scrolling — so pxPerHour is computed per-render
// from that measured width rather than being a fixed constant. All three
// panels are measured/sized identically, which keeps them aligned without
// any extra coordinate translation. Moving to the previous/next day slides
// the window itself (see GlucoseInsulinChart's day-paging), rather than
// scrolling within an oversized panel.

export const WINDOW_HOURS = 24;

/** px-per-hour for a panel measured at containerWidth px wide. */
export function pxPerHour(containerWidth: number): number {
  return containerWidth / WINDOW_HOURS;
}

/** X pixel position (within the panel's SVG) for a given time. */
export function xForTime(t: number, windowStart: number, pxPerHr: number): number {
  return ((t - windowStart) / 3_600_000) * pxPerHr;
}

/** The time at a given x pixel position, inverse of xForTime. */
export function timeForX(x: number, windowStart: number, pxPerHr: number): number {
  return windowStart + (x / pxPerHr) * 3_600_000;
}

/**
 * Binary-searches series (sorted ascending by time) for the entry whose
 * time is closest to target. Returns null for an empty series.
 */
export function nearest<T>(
  series: T[],
  target: number,
  timeOf: (item: T) => number
): T | null {
  if (series.length === 0) return null;
  let lo = 0;
  let hi = series.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (timeOf(series[mid]) < target) lo = mid + 1;
    else hi = mid;
  }
  // lo is the first index with timeOf >= target; compare against lo-1 too.
  if (lo > 0) {
    const prev = series[lo - 1];
    const curr = series[lo];
    if (Math.abs(timeOf(prev) - target) <= Math.abs(timeOf(curr) - target)) {
      return prev;
    }
  }
  return series[lo];
}

/** Parses an API timestamp string (RFC3339) to epoch millis. */
export function parseTime(s: string): number {
  return new Date(s).getTime();
}

/**
 * A CGM reading's glucose range band. Conceptually mirrors the
 * backend's own in-range classification (health-api/handlers/
 * handlers.go's getDataInWindow: MIN_MMOL/STRICT_MAX_MMOL/
 * MEDICAL_MAX_MMOL), but this classifier keeps "elevated" and
 * "critically out of range" as distinct bands (amber vs. red) rather
 * than the backend's single in_range boolean, and stays independent of
 * the backend's raw HSL point_color string so the chart can map each
 * band to its own (Okabe-Ito colorblind-safe) palette.
 */
export type CgmRange = "in-range" | "elevated" | "critical";

/**
 * Classifies a glucose reading into in-range/elevated/critical bands:
 *   mmol < min or mmol > max      -> "critical"  (below low, or above the outer/medical ceiling)
 *   min <= mmol <= strictMax      -> "in-range"  (the tight target band)
 *   strictMax < mmol <= max       -> "elevated"  (above target, not yet critical)
 * Boundaries are inclusive at min/strictMax/max, matching the
 * backend's `mmol >= minMmol && mmol <= strictMaxMmol` for in-range.
 */
export function cgmRange(mmol: number, min: number, strictMax: number, max: number): CgmRange {
  if (mmol < min || mmol > max) return "critical";
  if (mmol <= strictMax) return "in-range";
  return "elevated";
}

/** Midnight (local time) epoch millis for a YYYY-MM-DD date string. */
export function dayStart(dateStr: string): number {
  const [y, m, d] = dateStr.split("-").map(Number);
  return new Date(y, m - 1, d, 0, 0, 0, 0).getTime();
}
