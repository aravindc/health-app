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
