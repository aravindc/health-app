import {
  cgmRange,
  DEFAULT_MIN_MMOL,
  DEFAULT_STRICT_MAX_MMOL,
  DEFAULT_MAX_MMOL,
} from "../chart/scale";
import { colorForCgmRange } from "../chart/cgmColors";
import type { Forecast, ForecastPoint } from "../chart/forecast";

// The main chart's forecast colour (GlucoseInsulinChart's COLOR_FORECAST):
// the card is that forecast in miniature.
const COLOR_FORECAST = "#56b4e9";
const CARD_SURFACE = "#252525"; // .predicted-glucose background, for the dots' ring

// Plot geometry, in SVG user units (the SVG scales to the card's width).
const W = 240;
const H = 104;
const PAD = { left: 26, right: 26, top: 22, bottom: 18 };
// The y-axis spans at least this much, so a 0.1 mmol/L change doesn't look
// like a cliff.
const MIN_SPAN_MMOL = 2;

interface Props {
  // Predicted glucose (research model), or null when there's none.
  forecast: Forecast | null;
}

const rangeColor = (v: number) =>
  colorForCgmRange(cgmRange(v, DEFAULT_MIN_MMOL, DEFAULT_STRICT_MAX_MMOL, DEFAULT_MAX_MMOL));

const describe = (p: ForecastPoint) =>
  `${p.value.toFixed(1)} mmol/L in ${p.minutes} minutes (80% range ${p.lower.toFixed(1)} to ${p.upper.toFixed(1)})`;

/** The header's forecast card: a miniature of the chart's forecast, from the
 * current reading to the 15- and 30-minute predictions, with the 80% range
 * shaded. Points are coloured by range like the dial; each shows its value
 * and range on hover. There's no forecast for a stale reading: health-api
 * answers "not available" once the latest reading is 15 minutes old, and
 * the dashboard refetches the prediction at least every 5 minutes. */
export default function PredictedGlucose({ forecast }: Props) {
  if (!forecast) return null;
  const pts = forecast.points;
  const [now, ...ahead] = pts;

  // Scales: evenly spaced points (they're evenly spaced in time), and a y
  // domain fitting the 80% band, padded and at least MIN_SPAN_MMOL tall.
  const x = (i: number) => PAD.left + (i * (W - PAD.left - PAD.right)) / (pts.length - 1);
  let lo = Math.min(...pts.map((p) => p.lower));
  let hi = Math.max(...pts.map((p) => p.upper));
  if (hi - lo < MIN_SPAN_MMOL) {
    const mid = (hi + lo) / 2;
    lo = mid - MIN_SPAN_MMOL / 2;
    hi = mid + MIN_SPAN_MMOL / 2;
  }
  const y = (v: number) => PAD.top + ((hi - v) / (hi - lo)) * (H - PAD.top - PAD.bottom);

  const band = [
    ...pts.map((p, i) => `${x(i)},${y(p.upper)}`),
    ...pts.map((p, i) => `${x(i)},${y(p.lower)}`).reverse(),
  ].join(" ");
  const line = pts.map((p, i) => `${x(i)},${y(p.value)}`).join(" ");
  const summary = `Predicted glucose: ${ahead.map(describe).join("; ")}. Now ${now.value.toFixed(1)} mmol/L.`;

  return (
    <div
      className="predicted-glucose"
      title={`Predicted by a research model, with 80% ranges. ${forecast.disclaimer}`}
    >
      <span className="predicted-glucose__title">Forecast</span>
      <svg
        className="predicted-glucose__chart"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={summary}
      >
        <polygon points={band} fill={COLOR_FORECAST} opacity={0.22} />
        <polyline
          points={line}
          fill="none"
          stroke={COLOR_FORECAST}
          strokeWidth={2}
          strokeDasharray="5 4"
          strokeLinecap="round"
        />
        {pts.map((p, i) => (
          <g key={p.minutes} className="predicted-glucose__point">
            <circle
              cx={x(i)}
              cy={y(p.value)}
              r={4.5}
              fill={rangeColor(p.value)}
              stroke={CARD_SURFACE}
              strokeWidth={2}
            />
            {/* Value above the point's band (the current value is muted:
                it's measured, not predicted). */}
            <text
              x={x(i)}
              y={Math.max(12, y(p.upper) - 7)}
              textAnchor="middle"
              className={i === 0 ? "predicted-glucose__now" : "predicted-glucose__value"}
            >
              {p.value.toFixed(1)}
            </text>
            <text x={x(i)} y={H - 4} textAnchor="middle" className="predicted-glucose__axis">
              {i === 0 ? "now" : `${p.minutes} min`}
            </text>
            {/* A hover target larger than the dot. */}
            <circle cx={x(i)} cy={y(p.value)} r={12} fill="transparent">
              <title>
                {i === 0 ? `Now: ${p.value.toFixed(1)} mmol/L` : describe(p)}
              </title>
            </circle>
          </g>
        ))}
      </svg>
      <span className="predicted-glucose__note">Research model · 80% range shaded</span>
    </div>
  );
}
