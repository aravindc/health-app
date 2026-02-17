import type { LastReading } from "../api/types";

const TREND_ARROWS: Record<number, string> = {
  1: "\u2191\u2191", // DoubleUp
  2: "\u2191",       // SingleUp
  3: "\u2197",       // FortyFiveUp
  4: "\u2192",       // Flat
  5: "\u2198",       // FortyFiveDown
  6: "\u2193",       // SingleDown
  7: "\u2193\u2193", // DoubleDown
  8: "?",            // NotComputable
  9: "-",            // None
};

interface Props {
  reading: LastReading | null;
}

export default function CurrentReading({ reading }: Props) {
  if (!reading) {
    return (
      <div className="current-reading">
        <span className="current-reading__value">--</span>
      </div>
    );
  }

  const arrow = TREND_ARROWS[reading.bg_trend] ?? "?";
  const diff = reading.bg_mmol_diff;
  const diffStr = diff >= 0 ? `+${diff.toFixed(1)}` : diff.toFixed(1);

  const isLow = reading.bg_mmol < 4.0;
  const isHigh = reading.bg_mmol > 10.0;
  const colorClass = isLow
    ? "current-reading--low"
    : isHigh
      ? "current-reading--high"
      : "current-reading--normal";

  const readingTime = new Date(reading.bg_time * 1000);
  const now = new Date();
  const minsAgo = Math.round((now.getTime() - readingTime.getTime()) / 60000);
  const timeLabel =
    minsAgo < 1 ? "just now" : `${minsAgo} min ago`;

  return (
    <div className={`current-reading ${colorClass}`}>
      <div className="current-reading__main">
        <span className="current-reading__value">
          {reading.bg_mmol.toFixed(1)}
        </span>
        <span className="current-reading__arrow">{arrow}</span>
        <span className="current-reading__diff">
          {diffStr}
          <small> mmol/L</small>
        </span>
      </div>
      <div className="current-reading__meta">{timeLabel}</div>
    </div>
  );
}
