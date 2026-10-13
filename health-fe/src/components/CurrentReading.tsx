import type { CSSProperties } from "react";
import type { LastReading } from "../api/types";
import {
  cgmRange,
  DEFAULT_MIN_MMOL,
  DEFAULT_STRICT_MAX_MMOL,
  DEFAULT_MAX_MMOL,
} from "../chart/scale";
import { colorForCgmRange } from "../chart/cgmColors";

// Trend codes (as stored by health-sync's TrendToDirection) → where the
// pointer on the circle's edge points (degrees clockwise from "steady", i.e.
// pointing right) and how many chevrons it has. Codes 0/8/9, and 99 or any
// other unknown code, have no direction, so no pointer is drawn.
const TRENDS: Record<number, { label: string; angle?: number; chevrons?: number }> = {
  0: { label: "no trend" },                                // NONE
  1: { label: "rising quickly", angle: -90, chevrons: 2 }, // DoubleUp
  2: { label: "rising", angle: -90, chevrons: 1 },         // SingleUp
  3: { label: "rising slowly", angle: -45, chevrons: 1 },  // FortyFiveUp
  4: { label: "steady", angle: 0, chevrons: 1 },           // Flat
  5: { label: "falling slowly", angle: 45, chevrons: 1 },  // FortyFiveDown
  6: { label: "falling", angle: 90, chevrons: 1 },         // SingleDown
  7: { label: "falling quickly", angle: 90, chevrons: 2 }, // DoubleDown
  8: { label: "trend unavailable" },                       // NotComputable
  9: { label: "rate out of range" },                       // RATE OUT OF RANGE
};

// Pointer geometry, in a viewBox where the dial is a circle of radius R
// centred on the origin and the pointer points along +x (rotated into place
// with CSS). The bezel extends into a teardrop: two lines tangent to the
// circle meet at a ~90° tip TIP from the centre. The arrow is the end of the
// teardrop inset by ARROW_PAD on every side, so bezel shows all round it:
// its sides run parallel to the teardrop's, and its base is cut at radius
// ARROW_R so it curves with the circle.
const R = 50;
const TIP = 70;
const ARROW_PAD = 4;
const ARROW_R = 52.3; // a little outside the circle (R)
const TX = (R * R) / TIP; // tangent points (TX, ±TY)
const TY = Math.sqrt(R * R - TX * TX);
// Insetting both sides by ARROW_PAD moves the tip back by ARROW_PAD / sin(half
// the tip angle), and sin(half tip angle) = R / TIP.
const ARROW_TIP = TIP - (ARROW_PAD * TIP) / R;
// Where the inset side (from ARROW_TIP, parallel to TIP→tangent) crosses
// radius ARROW_R: |ARROW_TIP + t·(TX−TIP, TY)| = ARROW_R.
const a = (TX - TIP) ** 2 + TY ** 2;
const b = 2 * ARROW_TIP * (TX - TIP);
const c = ARROW_TIP ** 2 - ARROW_R ** 2;
const t = (-b - Math.sqrt(b * b - 4 * a * c)) / (2 * a);
const AX = ARROW_TIP + t * (TX - TIP); // arrow base corners (AX, ±AY)
const AY = t * TY;
const TEARDROP = `M ${TIP} 0 L ${TX} ${-TY} A ${R} ${R} 0 1 0 ${TX} ${TY} Z`;
const ARROW = `M ${ARROW_TIP} 0 L ${AX} ${-AY} A ${ARROW_R} ${ARROW_R} 0 0 1 ${AX} ${AY} Z`;
// Rapid changes add a second, free-standing arrow beyond the tip.
const SECOND_ARROW = `M ${TIP + 15} 0 L ${TIP + 4} -11 L ${TIP + 4} 11 Z`;

// With readings every 5 minutes, this many minutes without one (three
// missed) means the sensor or sync has stopped: the dial greys out and the
// time gets a warning so an old reading isn't taken as current.
const STALE_MINUTES = 15;

interface Props {
  reading: LastReading | null;
}

export default function CurrentReading({ reading }: Props) {
  if (!reading) {
    return (
      <div className="current-reading current-reading--none">
        <div className="current-reading__dial">
          <div className="current-reading__circle">
            <span className="current-reading__value">--</span>
          </div>
        </div>
      </div>
    );
  }

  const trend = TRENDS[reading.bg_trend] ?? TRENDS[8];
  const diff = reading.bg_mmol_diff;
  // Change since the previous reading: "± 0.0" when it rounds to zero,
  // otherwise ▲/▼ and the size of the change.
  const diffAbs = Math.abs(diff).toFixed(1);
  const [diffMark, diffWord] =
    diffAbs === "0.0" ? ["±", "no change"] : diff > 0 ? ["▲", "up"] : ["▼", "down"];
  // Outside the sensor's 40–400 mg/dL range Dexcom reports LOW/HIGH
  // rather than a number (sent as 39 and 401 mg/dL).
  const word =
    reading.bg_mmol < 2.2 ? "LOW" : reading.bg_mmol > 22.2 ? "HIGH" : null;
  const value = word ?? reading.bg_mmol.toFixed(1);

  // Same bands and colours as the chart's CGM bars.
  const range = cgmRange(
    reading.bg_mmol,
    DEFAULT_MIN_MMOL,
    DEFAULT_STRICT_MAX_MMOL,
    DEFAULT_MAX_MMOL
  );
  const colorStyle = { "--reading-color": colorForCgmRange(range) } as CSSProperties;

  const readingTime = new Date(reading.bg_time * 1000);
  const now = new Date();
  const minsAgo = Math.round((now.getTime() - readingTime.getTime()) / 60000);
  const isStale = minsAgo >= STALE_MINUTES;
  const timeLabel =
    minsAgo < 1 ? "just now" : `${isStale ? "⚠ " : ""}${minsAgo} min ago`;

  return (
    <div
      className={`current-reading current-reading--${range}${isStale ? " current-reading--stale" : ""}`}
      style={colorStyle}
    >
      <div
        className="current-reading__dial"
        role="img"
        aria-label={`${word ?? `${value} mmol/L`}, ${trend.label}${
          isStale ? `, ${minsAgo} min old` : ""
        }`}
      >
        {trend.angle !== undefined && (
          <svg
            className="current-reading__pointer"
            viewBox={`${-R} ${-R} ${2 * R} ${2 * R}`}
            style={{ transform: `rotate(${trend.angle}deg)` }}
            aria-hidden="true"
          >
            <path className="current-reading__bezel" d={TEARDROP} />
            <path className="current-reading__chevron" d={ARROW} />
            {trend.chevrons === 2 && (
              <path className="current-reading__chevron" d={SECOND_ARROW} />
            )}
          </svg>
        )}
        <div className="current-reading__circle">
          {word ? (
            <span className="current-reading__value current-reading__value--word">
              {word}
            </span>
          ) : (
            <span className="current-reading__value">{value}</span>
          )}
        </div>
      </div>
      <div className="current-reading__info">
        <span className="current-reading__diff">
          <span aria-hidden="true">{diffMark} </span>
          <span className="current-reading__sr">{diffWord} </span>
          {diffAbs}
        </span>
        <span className="current-reading__meta">{timeLabel}</span>
      </div>
    </div>
  );
}
