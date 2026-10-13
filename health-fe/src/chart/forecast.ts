// Predicted glucose (GET /prediction, from the health-predict research model)
// as points for the chart and the header readout. Only the 15- and
// 30-minute predictions are shown: further out the intervals get wide.
import type { Prediction } from "../api/types";

/** How far past the latest reading the forecast (and the chart) reaches. */
export const FORECAST_MINUTES = 30;
const HORIZONS = ["15m", "30m"] as const;

/** A forecast point: predicted mmol/L with its 80% and 95% intervals. */
export interface ForecastPoint {
  time: number; // epoch ms
  minutes: number; // after the reading the prediction is from
  value: number;
  lower: number;
  upper: number;
  lower95: number;
  upper95: number;
}

export interface Forecast {
  /** The reading it starts from (zero-width interval), then +15 and +30 min. */
  points: ForecastPoint[];
  warnings: string[];
  disclaimer: string;
}

/** The forecast to show, or null when there's no usable prediction. */
export function toForecast(p: Prediction | null): Forecast | null {
  if (!p || !p.available) return null;
  const at = new Date(p.timestamp).getTime();
  if (Number.isNaN(at)) return null;
  const g = p.current_glucose;
  const points: ForecastPoint[] = [
    { time: at, minutes: 0, value: g, lower: g, upper: g, lower95: g, upper95: g },
  ];
  for (const h of HORIZONS) {
    const iv = p.predictions[h];
    if (!iv) return null;
    const minutes = parseInt(h, 10);
    points.push({
      time: at + minutes * 60_000,
      minutes,
      value: iv.value,
      lower: iv.lower,
      upper: iv.upper,
      lower95: iv.lower_95,
      upper95: iv.upper_95,
    });
  }
  return { points, warnings: p.warnings, disclaimer: p.disclaimer };
}

/** The forecast point nearest `time`, if `time` falls in the forecast. */
export function forecastAt(f: Forecast, time: number): ForecastPoint | null {
  const first = f.points[0].time;
  const last = f.points[f.points.length - 1].time;
  if (time <= first || time > last + 2.5 * 60_000) return null;
  return f.points
    .slice(1)
    .reduce((a, b) => (Math.abs(b.time - time) < Math.abs(a.time - time) ? b : a));
}
