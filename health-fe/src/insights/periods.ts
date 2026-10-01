// Periods an Insights card can be switched between (via PeriodDialog), and
// how each maps onto the API's parameters.

export type PeriodId = "24h" | "7d" | "14d" | "90d";

export interface Period {
  id: PeriodId;
  short: string; // dialog option, e.g. "24H"
  label: string; // card header, e.g. "24 hours"
  long: string; // descriptions, e.g. "last 24 hours"
  hours: number; // /insightstats/:hours
  days: number; // /gmi/:days
}

export const PERIODS: Period[] = [
  { id: "24h", short: "24H", label: "24 hours", long: "last 24 hours", hours: 24, days: 1 },
  { id: "7d", short: "7d", label: "7 days", long: "last 7 days", hours: 168, days: 7 },
  { id: "14d", short: "14d", label: "14 days", long: "last 14 days", hours: 336, days: 14 },
  { id: "90d", short: "90d", label: "90 days", long: "last 90 days", hours: 2160, days: 90 },
];

export const periodById = (id: PeriodId): Period => PERIODS.find((p) => p.id === id)!;
