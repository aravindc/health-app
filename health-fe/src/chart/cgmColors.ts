// Status colours for CGM readings, shared by GlucoseInsulinChart's reading
// bars and CurrentReading's dial so a reading is the same colour in both.
// Kept out of those component files for Vite Fast Refresh (see
// heatmapColors.ts).

import type { CgmRange } from "./scale";

// Colored per-reading by range (see scale.ts's cgmRange): green for the
// tight target band, amber for elevated-but-not-critical, red for below
// the low threshold or above the medical ceiling. This is a *status*
// palette (good/warning/critical), not a categorical one — reusing the
// chart's COLOR_LOW/COLOR_HIGH (adjacent Okabe-Ito hues, normal-vision
// ΔE ~15) was tried first and rejected: amber and red need to be
// unmistakable per-bar at a glance across a dense 24h window, and those
// two sit too close together (validated via dataviz skill's
// validate_palette.js — see PR description). These three are a fixed,
// pre-validated status set (ΔE ~27+ between adjacent pairs on the dark
// surface the chart renders on), no unused warning/serious/critical
// categories here.
export const COLOR_CGM_IN_RANGE = "#0ca30c"; // green (status: good)
export const COLOR_CGM_ELEVATED = "#fab219"; // amber (status: warning)
export const COLOR_CGM_CRITICAL = "#d03b3b"; // red (status: critical)

/** Maps a CGM reading's range band to its colour. */
export function colorForCgmRange(range: CgmRange): string {
  switch (range) {
    case "in-range":
      return COLOR_CGM_IN_RANGE;
    case "elevated":
      return COLOR_CGM_ELEVATED;
    case "critical":
      return COLOR_CGM_CRITICAL;
  }
}
