import type { CSSProperties } from "react";
import {
  cgmRange,
  DEFAULT_MIN_MMOL,
  DEFAULT_STRICT_MAX_MMOL,
  DEFAULT_MAX_MMOL,
} from "../chart/scale";
import { colorForCgmRange } from "../chart/cgmColors";
import type { Forecast } from "../chart/forecast";

// A prediction from a reading this old would be from stale data: hide it
// (the same limit as the dial's stale warning and health-api's).
const STALE_MINUTES = 15;

interface Props {
  // Predicted glucose (research model), or null when there's none.
  forecast: Forecast | null;
}

/** The header's "Predicted" block: the 15- and 30-minute predictions,
 * coloured by range like the dial, with their 80% ranges and the research
 * disclaimer on hover. */
export default function PredictedGlucose({ forecast }: Props) {
  if (!forecast) return null;
  const [reading, ...ahead] = forecast.points;
  if ((Date.now() - reading.time) / 60_000 >= STALE_MINUTES) return null;

  return (
    <div
      className="predicted-glucose"
      title={`Predicted by a research model, with 80% ranges. ${forecast.disclaimer}`}
    >
      <span className="predicted-glucose__title">Predicted</span>
      {ahead.map((p) => (
        <span
          key={p.minutes}
          className="predicted-glucose__row"
          aria-label={`${p.value.toFixed(1)} mmol/L in ${p.minutes} minutes, 80% range ${p.lower.toFixed(1)} to ${p.upper.toFixed(1)}`}
        >
          <span
            className="predicted-glucose__value"
            style={
              {
                "--predicted-color": colorForCgmRange(
                  cgmRange(p.value, DEFAULT_MIN_MMOL, DEFAULT_STRICT_MAX_MMOL, DEFAULT_MAX_MMOL)
                ),
              } as CSSProperties
            }
          >
            {p.value.toFixed(1)}
          </span>{" "}
          in {p.minutes} mins
        </span>
      ))}
    </div>
  );
}
