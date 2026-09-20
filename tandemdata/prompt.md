# Prompt: Glucose & Insulin Trace chart

Paste the prompt below (adjust the chunk filename) to regenerate the CGM /
basal / bolus-activity artifact from a `tandemdata` chunk file, without
re-deriving the data quirks and design decisions this took a few rounds to
land on.

---

## Prompt

Read `data/chunks/<START>_<END>.json` (a tandemdata pump-logs chunk: top-level
`events` array, each with `eventCode`, `pumpDateTime`, `eventProperties`) and
build a single combined dark-themed chart artifact with three time-aligned,
horizontally-scrollable panels sharing one 24-hour viewport and a synced
crosshair:

1. **Sensor glucose** — CGM line in **mmol/L** (convert mg/dL by dividing by
   18.0182), with:
   - target range 3.9–10.0 mmol/L drawn as a **dashed outline**, not a filled
     band (a filled band hides the line where it crosses it); shade low
     (<3.9) and high (>10.0) zones outside it instead.
   - a dot at each bolus dose's timestamp, positioned along the top of the
     panel, colored by which category dominates that dose (see palette
     below): orange = food-dominant, green = correction-dominant.
   - a faint dashed vertical line at each dose timestamp, threaded through
     the full panel height (and reused on the activity panel below), colored
     to match the dose dot.
2. **Bolus insulin activity** — food and correction modeled insulin *activity*
   (not raw dose amounts) as two **un-stacked, overlapping** filled areas
   sharing one zero baseline (not stacked on top of each other — stacking
   hides the smaller series and distorts its shape by riding a moving
   baseline).
3. **Basal delivery** — commanded basal rate (U/hr) as a step area, its own
   panel, own y-scale.

Un-stacked, not stacked: draw each series (food, correction) as its own area
from y=0, both on the panel's shared y-axis. Do not sum one on top of the
other.

### Data model (tandemdata pump-logs event codes)

- **CGM**: `eventCode 399`, `eventProperties.currentGlucoseDisplayValue` is
  mg/dL. Skip non-positive values.
- **Basal**: `eventCode 279`, `eventProperties.commandedRate` is in
  **milliunits/hr — divide by 1000 for U/hr**. (Verified against `eventCode
  81`'s `eventProperties.lastBasalRate`, which is already in true U/hr, at
  matching timestamps — the ratio is exactly 1000, not 100. Don't assume
  centiunits without checking; cross-check any new export the same way.)
- **Bolus food/correction split**: `eventCode 66`
  (`BolusRequestedSplit`), keyed by `eventProperties.bolusId` ->
  `{foodBolusSize, correctionBolusSize, totalBolusSize}`.
- **Bolus delivered amount**: `eventCode 20` (`BolusCompleted`) and `21`
  (`BolusCompleted2`, the extended/combo-bolus "later" portion — a second
  completion event for the same `bolusId`, not a duplicate to be
  deduplicated). `eventProperties.insulinDelivered` is the delivered units
  for that completion event. Join to the code-66 split by `bolusId`, split
  each delivered chunk proportionally by `food/total` and
  `correction/total`, and treat both completions (now + extended) as
  separate delivery timestamps when building the activity curve, each
  starting its own decay from when it was actually delivered.
- If a delivered bolus has no matching code-66 split record, treat it as
  100% food rather than dropping it (should be rare/never in practice; log
  it if it happens).

### Insulin activity curve — get the shape right

Model each dose's activity over the 4 hours after delivery as a **raised
cosine bell**: `weight(h) = sin(π·h/4)²` for `h` in `[0, 4]`, `0` outside.

- This peaks at `h=2` and is **exactly zero at both h=0 and h=4** by
  construction — no artificial truncation.
- Do **not** use an exponential-decay shape like `h·e^(−h/τ)` truncated at 4
  hours: with `τ=2` (peak at h=2), that curve is still at ~73% of its peak
  height at h=4, so cutting it off there isn't a bell, it's a ramp — doses
  then blur into one continuous plateau instead of showing separated humps
  when overlapping.
- Discretize at 5-minute steps, normalize the discretized weights so they sum
  to 1, then each dose's curve = `delivered_units * normalized_weight[i]` at
  each step — this makes the curve's own integral exactly recover the
  delivered dose (verify this in the extraction script's stderr output).
- **Grid-align dose start times before building each curve.** A dose's raw
  timestamp lands on an arbitrary second (e.g. `06:19:54`). Stepping forward
  in 5-minute increments from that exact instant puts every dose's curve on
  its own uncoordinated timing grid; when two overlapping doses' curves are
  summed by timestamp key, they then almost never share a key, so instead of
  adding cleanly they interleave as separate points 1–4 minutes apart — a
  straight-line chart renders that as a jagged sawtooth. **Snap each dose's
  start time to the nearest 5-minute mark before stepping**, so every dose
  sits on the same shared grid and overlapping curves sum at identical
  timestamps. Verify by checking the gap distribution between consecutive
  points in the output series — it should be uniformly 5 minutes (or a clean
  multiple, for genuine no-activity gaps), never 1–4 minutes.
- Doses closer together than 4 hours will sum into a wider or multi-peaked
  shape — that's correct overlapping insulin action, not a bug. Say so in
  the chart's caption so it doesn't read as broken.

### Stat tiles (compute independently, don't hardcode)

Avg glucose, % time-in-range (3.9–10.0 mmol/L), % below 3.9, % above 10.0,
total food bolus units, total correction bolus units, total basal units
(trapezoidal integration over the commanded-rate samples — not a simple
average x hours, since rate samples are irregular), and average total daily
insulin (basal + bolus, normalized by the actual day-span of the data, not
assumed to be exactly N days).

### Layout & interaction

- Three panels, each an SVG drawn at full time-proportional width (fixed
  px-per-hour), wrapped in an `overflow-x: auto` container capped to a 24h
  window's pixel width — this makes each panel scrollable and all three
  scroll in sync (mirror `scrollLeft` across the three containers).
- Prev/Next 24h buttons plus a "Latest" jump, paging by the container's
  *actual visible width* (not a hardcoded 24h-in-px), so paging is exact
  even when the viewport is narrower than the nominal window.
- One shared crosshair + tooltip across all three panels (vertical line +
  per-series readout), positioned by binary-searching each series for the
  nearest timestamp to the pointer.
- Default scroll position: most recent 24 hours.

### Visual design

- Dark theme by default (this is a "checked at 3am" instrument), light theme
  as an explicit override — both themes fully tokenized, never a color
  defined only inside one theme's block.
- Categorical palette: run candidates through the dataviz skill's
  `validate_palette.js` (CVD separation + contrast) rather than picking by
  eye — only the 3 series that actually co-occur on the same panel (CGM,
  food, correction) need to pass as a set; basal is alone in its own panel
  and can be a plain muted gray.
- IBM Plex Sans (UI text) + IBM Plex Mono (numerals, timestamps,
  `tabular-nums` for the stat tiles) — a technical/instrument-panel pairing
  appropriate to a data-dense clinical export, loaded from Google Fonts.
- No "Other/manual" bolus category unless the underlying data actually
  distinguishes it (check `bolusType`/`bolusSource` on `eventCode 280`
  before adding a category — don't fabricate one to match a reference image).

### Before publishing

- Cross-check any new/unfamiliar field's unit scale against a second,
  independently-computed source in the same export (as done here for
  `commandedRate` vs. `lastBasalRate`) rather than assuming a divisor.
- Verify the activity curve's integral matches total delivered units (log it).
- Verify the activity series' timestamp gaps are uniform before shipping —
  jaggedness in a smooth modeled curve is almost always a timing-grid bug,
  not a rendering one.
