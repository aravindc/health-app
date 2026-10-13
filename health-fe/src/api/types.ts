export interface LastReading {
  bg_time: number;
  bg_mmol: number;
  bg_trend: number;
  bg_mmol_diff: number;
}

export interface SparklinePoint {
  bg_time: number;
  bg_mmol: number;
}

export interface DataPoint {
  epoch: number;
  mmol: number;
  datetime: string;
  date: string;
  time: string;
  in_range: boolean;
  point_color: string;
}

// GET /insightstats/:hours: the Insights cards' reading-based values,
// computed by health-api (handlers/insights.go).
export interface RangeSplit {
  low: number; // % below the min
  in: number; // % within the range
  high: number; // % above the range
}

export interface InsightStats {
  hours: number;
  count: number;
  in_range_pct: number; // % within min–strict max, rounded on its own
  mean: number;
  median: number;
  std_dev: number;
  cv: number;
  q1: number;
  q3: number;
  highs: number;
  lows: number;
  unicorns: number;
  normal: RangeSplit;
  strict: RangeSplit;
  sparkline: SparklinePoint[]; // oldest first; bg_time in epoch ms
}

export interface DailyAvg {
  bg_date: string;
  bg_mmol: number;
}

export interface DailyTir {
  bg_date: string;
  pir_strict: number;
  pir_medical: number;
}

export interface AvgMmol {
  time_period: string;
  bg_mmol: number;
}

export interface QuartPoint {
  group: string;
  mu: number;
  sd: number;
  n: number;
  value: number;
}

export interface GmiResponse {
  gmi_percent: number;
  gmi_mmol: number;
}

export interface PercentInRange {
  id: string;
  data: { x: string; y: number }[];
}

export interface TimeInRange {
  hours: number;
  minutes: number;
}

export interface BolusDose {
  bolus_id: number;
  delivered_at: string;
  insulin_delivered: number;
  food_units: number;
  correction_units: number;
  // Carbs (grams) entered for this dose; null when none was recorded
  // (e.g. a correction-only bolus), not the same as 0g.
  carb_amount: number | null;
  dominant_category: "food" | "correction";
}

export interface ActivityPoint {
  time: string;
  units: number;
}

export interface BolusChart {
  doses: BolusDose[];
  food_activity: ActivityPoint[];
  correction_activity: ActivityPoint[];
}

export interface BasalPoint {
  time: string;
  commanded_rate: number;
}

export interface BasalChart {
  points: BasalPoint[];
  window_end: string;
}

/** One horizon's predicted glucose (mmol/L) with its 80% (lower/upper) and
 * 95% intervals. */
export interface PredictionInterval {
  value: number;
  lower: number;
  upper: number;
  lower_95: number;
  upper_95: number;
  confidence_level: number;
}

/** GET /prediction: predicted glucose after the latest reading, from the
 * health-predict research model, or why there is none. */
export type Prediction =
  | {
      available: true;
      timestamp: string;
      current_glucose: number;
      unit: "mmol/L";
      predictions: Partial<Record<"15m" | "30m" | "45m" | "60m", PredictionInterval>>;
      model_version: Record<string, string>;
      warnings: string[];
      disclaimer: string;
    }
  | { available: false; reason: string };
