interface Props {
  q1: number;
  median: number;
  q3: number;
  // No readings yet: draw the curve with "--" labels.
  empty: boolean;
}

// A bell built from y = sin²(πx/4) for x in [0, 4]: zero at both ends and
// peaking at x = 2. Q1, the median and Q3 sit at x = 1, 2, 3 (where the
// curve is at half height, its peak, and half height again), and the
// middle 50% between Q1 and Q3 is shaded.
//
// To show a lopsided spread, the two halves get different widths: the
// left half (x 0–2) takes a share of the width proportional to
// median − Q1, the right half (x 2–4) to Q3 − median. Each half is still
// half a sin² hump, so the curve stays smooth at the peak and Q1/Q3 stay
// at half height. The split is capped at MAX_SPLIT so the median label
// never lands on top of the Q1 or Q3 label.
const W = 120; // viewBox width
const PAD_X = 4;
const INNER = W - 2 * PAD_X;
const TOP = 6; // y of the peak
const BASE = 40; // y of the baseline
const SAMPLES = 64;
const MAX_SPLIT = 0.65;

/** Share of the width taken by the left half (lower tail to median). */
function leftShare(q1: number, median: number, q3: number): number {
  const below = Math.max(0, median - q1);
  const above = Math.max(0, q3 - median);
  if (below + above === 0) return 0.5;
  return Math.min(MAX_SPLIT, Math.max(1 - MAX_SPLIT, below / (below + above)));
}

const yToSvg = (x: number) => BASE - Math.sin((Math.PI * x) / 4) ** 2 * (BASE - TOP);

/** Maps curve x (0–4) to SVG x, with the left half `share` of the width. */
function xToSvg(x: number, share: number): number {
  return x <= 2
    ? PAD_X + (x / 2) * share * INNER
    : PAD_X + share * INNER + ((x - 2) / 2) * (1 - share) * INNER;
}

/** SVG points along the curve from x = from to x = to. */
function curvePoints(from: number, to: number, share: number): string {
  const n = Math.max(2, Math.round((SAMPLES * (to - from)) / 4));
  return Array.from({ length: n + 1 }, (_, i) => {
    const x = from + ((to - from) * i) / n;
    return `${xToSvg(x, share).toFixed(2)},${yToSvg(x).toFixed(2)}`;
  }).join(" L ");
}

export default function QuartileCurve({ q1, median, q3, empty }: Props) {
  const label = (v: number) => (empty ? "--" : v.toFixed(1));
  const share = empty ? 0.5 : leftShare(q1, median, q3);
  const curve = `M ${curvePoints(0, 4, share)}`;
  // Area under the curve between Q1 (x = 1) and Q3 (x = 3).
  const iqrArea =
    `M ${xToSvg(1, share)},${BASE} L ${curvePoints(1, 3, share)} ` +
    `L ${xToSvg(3, share)},${BASE} Z`;
  const markers = [
    { x: 1, value: q1, name: "Q1", mid: false },
    { x: 2, value: median, name: "Median", mid: true },
    { x: 3, value: q3, name: "Q3", mid: false },
  ];
  return (
    <svg
      className="quartile-curve"
      viewBox={`0 0 ${W} 54`}
      role="img"
      aria-label={
        empty
          ? "Quartiles: no data"
          : `Quartiles: Q1 ${label(q1)}, median ${label(median)}, Q3 ${label(q3)} mmol/L`
      }
    >
      <path className="quartile-curve__iqr" d={iqrArea} />
      <line className="quartile-curve__base" x1={xToSvg(0, share)} y1={BASE} x2={xToSvg(4, share)} y2={BASE} />
      <path className="quartile-curve__line" d={curve} />
      {markers.map((m) => (
        <g key={m.name}>
          <line
            className={`quartile-curve__marker${m.mid ? " quartile-curve__marker--mid" : ""}`}
            x1={xToSvg(m.x, share)}
            y1={BASE}
            x2={xToSvg(m.x, share)}
            y2={yToSvg(m.x)}
          />
          <text
            className={`quartile-curve__label${m.mid ? " quartile-curve__label--mid" : ""}`}
            x={xToSvg(m.x, share)}
            y={BASE + 11}
          >
            {label(m.value)}
          </text>
        </g>
      ))}
    </svg>
  );
}
