import { useState } from "react";
import InsightCard from "./InsightCard";
import MiniSparkline from "./MiniSparkline";
import QuartileCurve from "./QuartileCurve";
import { api } from "../api/client";
import { useCachedFetch, type Fetched } from "../api/useCachedFetch";
import { periodById, type PeriodId } from "../insights/periods";
import type {
  GmiResponse,
  InsightStats,
  RangeSplit,
} from "../api/types";

interface Props {
  // App's default-period data, refreshed every minute: the 24h values and
  // the 90-day GMI. Other periods are fetched here when a card picks one.
  stats24h: InsightStats | null;
  gmi: GmiResponse | null;
  // App's last refresh (epoch ms); prompts stale non-default data to refetch.
  refreshedAt: number;
}

type CardId =
  | "pir" | "avg" | "mini" | "quart" | "normal" | "strict" | "gmi"
  | "unicorns" | "highsLows" | "median" | "stdDev" | "cv" | "flux";

const DEFAULT_PERIODS: Record<CardId, PeriodId> = {
  pir: "24h", avg: "24h", mini: "24h", quart: "24h", normal: "24h", strict: "24h",
  gmi: "90d", unicorns: "24h", highsLows: "24h", median: "24h", stdDev: "24h",
  cv: "24h", flux: "24h",
};

// Cards whose values come from /insightstats (health-api computes them from
// the period's readings); they share one fetch per period.
const STATS_CARDS: CardId[] = [
  "pir", "avg", "mini", "quart", "normal", "strict", "unicorns", "highsLows", "median", "stdDev", "cv", "flux",
];

const EMPTY_STATS: InsightStats = {
  hours: 0, count: 0, in_range_pct: 0, mean: 0, median: 0, std_dev: 0, cv: 0, q1: 0, q3: 0,
  highs: 0, lows: 0, unicorns: 0,
  normal: { low: 0, in: 100, high: 0 },
  strict: { low: 0, in: 100, high: 0 },
  sparkline: [],
};

// /gmi/:days rejects 7 days or fewer.
const GMI_TOO_SHORT = "GMI needs more than 7 days of readings.";
const GMI_DISABLED: Partial<Record<PeriodId, string>> = { "24h": GMI_TOO_SHORT, "7d": GMI_TOO_SHORT };

function getFluxGrade(cv: number): string {
  if (cv <= 20) return "A+";
  if (cv <= 25) return "A";
  if (cv <= 30) return "B+";
  if (cv <= 33) return "B";
  if (cv <= 36) return "C";
  return "D";
}

function RangeBar({ split }: { split: RangeSplit }) {
  return (
    <div className="range-bar">
      <span className="range-bar__low" style={{ flex: split.low }}>
        {split.low}%
      </span>
      <span className="range-bar__in" style={{ flex: split.in }}>
        {split.in}%
      </span>
      <span className="range-bar__high" style={{ flex: split.high }}>
        {split.high}%
      </span>
    </div>
  );
}

const ready = <T,>(data: T): Fetched<T> => ({ data, loading: false, error: null });

export default function InsightsGrid({
  stats24h,
  gmi,
  refreshedAt,
}: Props) {
  const [periods, setPeriods] = useState(DEFAULT_PERIODS);
  const setPeriod = (card: CardId) => (period: PeriodId) =>
    setPeriods((prev) => ({ ...prev, [card]: period }));
  const p = (card: CardId) => periodById(periods[card]);

  // /insightstats for each period some stats-based card is showing.
  const used = (id: PeriodId) => STATS_CARDS.some((c) => periods[c] === id);
  const statsKey = (id: PeriodId) => (used(id) ? `insightstats/${periodById(id).hours}` : null);
  const stats: Record<PeriodId, Fetched<InsightStats>> = {
    "24h": stats24h ? ready(stats24h) : { data: null, loading: true, error: null },
    "7d": useCachedFetch(statsKey("7d"), () => api.getInsightStats(168), refreshedAt),
    "14d": useCachedFetch(statsKey("14d"), () => api.getInsightStats(336), refreshedAt),
    "90d": useCachedFetch(statsKey("90d"), () => api.getInsightStats(2160), refreshedAt),
  };
  // Until a period's stats arrive the card shows "Loading…", so these
  // zeros are never displayed.
  const statsFor = (card: CardId): InsightStats => stats[periods[card]].data ?? EMPTY_STATS;
  const statusFor = (card: CardId) => {
    const r = stats[periods[card]];
    return { loading: r.loading, error: r.data ? null : r.error };
  };

  // Server-computed cards: App's data for the default period, otherwise a
  // fetch for the chosen one.
  const pirStrict = statsFor("pir").in_range_pct;

  const gmiFetch = useCachedFetch(
    periods.gmi === "90d" ? null : `gmi/${p("gmi").days}`,
    () => api.getGmi(p("gmi").days),
    refreshedAt
  );
  const gmiData = periods.gmi === "90d" ? ready(gmi) : gmiFetch;

  const status = <T,>(f: Fetched<T>) => ({ loading: f.loading, error: f.data ? null : f.error });

  const quart = statsFor("quart");

  // Props every card shares: its period, and switching it.
  const card = (id: CardId) => ({ period: periods[id], onPeriodChange: setPeriod(id) });

  return (
    <div className="insights">
      <h2 className="insights__title">Insights</h2>
      <div className="insights__grid">
        <InsightCard
          title="% In Range"
          {...card("pir")}
          {...statusFor("pir")}
          description={`Share of readings in the ${p("pir").long} within the strict target range, 4.0–7.0 mmol/L.`}
        >
          <div className="insight-value insight-value--ring">
            <svg viewBox="0 0 36 36" className="ring-svg">
              <path
                className="ring-bg"
                d="M18 2.0845a 15.9155 15.9155 0 0 1 0 31.831 15.9155 15.9155 0 0 1 0 -31.831"
              />
              <path
                className="ring-fg"
                strokeDasharray={`${pirStrict}, 100`}
                d="M18 2.0845a 15.9155 15.9155 0 0 1 0 31.831 15.9155 15.9155 0 0 1 0 -31.831"
              />
              <text x="18" y="18" className="ring-text">
                {Math.round(pirStrict)}
              </text>
            </svg>
          </div>
        </InsightCard>

        <InsightCard
          title="Average Glucose"
          {...card("avg")}
          {...statusFor("avg")}
          description={`Mean of all readings in the ${p("avg").long}.`}
        >
          <div className="insight-value">
            <span className="insight-value__big">
              {statsFor("avg").count > 0 ? statsFor("avg").mean.toFixed(1) : "--"}
            </span>
            <span className="insight-value__unit">mmol/L</span>
          </div>
        </InsightCard>

        <InsightCard
          title="Mini Graph"
          {...card("mini")}
          {...statusFor("mini")}
          description={`Glucose trace over the ${p("mini").long}.`}
        >
          <MiniSparkline data={statsFor("mini").sparkline} />
        </InsightCard>

        <InsightCard
          title="Quartiles"
          {...card("quart")}
          {...statusFor("quart")}
          description={`25th, 50th (median, centre) and 75th percentile of the ${p("quart").long} of readings. Half of all readings fall between the outer two.`}
        >
          <QuartileCurve
            q1={quart.q1}
            median={quart.median}
            q3={quart.q3}
            empty={quart.count === 0}
          />
        </InsightCard>

        <InsightCard
          title="Normal Range %"
          {...card("normal")}
          {...statusFor("normal")}
          description={`Share of readings in the ${p("normal").long} below 4.0 (red), 4.0–10.0 (green) and above 10.0 mmol/L (amber).`}
        >
          <RangeBar split={statsFor("normal").normal} />
        </InsightCard>

        <InsightCard
          title="Strict Range %"
          {...card("strict")}
          {...statusFor("strict")}
          periodSuffix=" (4–7)"
          description={`Share of readings in the ${p("strict").long} below 4.0 (red), 4.0–7.0 (green) and above 7.0 mmol/L (amber).`}
        >
          <RangeBar split={statsFor("strict").strict} />
        </InsightCard>

        <InsightCard
          title="GMI"
          {...card("gmi")}
          {...status(gmiData)}
          disabledPeriods={GMI_DISABLED}
          description={`Glucose Management Indicator: an estimate of HbA1c from the ${p("gmi").days}-day average glucose (3.31 + 0.02392 × mean mg/dL).`}
        >
          <div className="insight-value">
            <span className="insight-value__big">
              {gmiData.data?.gmi_percent?.toFixed(1) ?? "--"}
            </span>
            <span className="insight-value__unit">%</span>
          </div>
        </InsightCard>

        <InsightCard
          title="Unicorns"
          {...card("unicorns")}
          {...statusFor("unicorns")}
          description={`Readings of exactly 5.5 mmol/L in the ${p("unicorns").long}.`}
        >
          <div className="insight-value">
            <span className="insight-value__big">{statsFor("unicorns").unicorns}</span>
            <span className="insight-value__unit">found</span>
          </div>
        </InsightCard>

        <InsightCard
          title="Highs / Lows"
          {...card("highsLows")}
          {...statusFor("highsLows")}
          description={`Number of readings in the ${p("highsLows").long} above 10.0 mmol/L (highs) and below 4.0 mmol/L (lows).`}
        >
          <div className="insight-value">
            <span className="insight-value__big">
              {statsFor("highsLows").highs} / {statsFor("highsLows").lows}
            </span>
          </div>
        </InsightCard>

        <InsightCard
          title="Median"
          {...card("median")}
          {...statusFor("median")}
          description={`Middle value of the ${p("median").long} of readings: half are above it, half below.`}
        >
          <div className="insight-value">
            <span className="insight-value__big">
              {statsFor("median").median.toFixed(1)}
            </span>
            <span className="insight-value__unit">mmol/L</span>
          </div>
        </InsightCard>

        <InsightCard
          title="Std. Dev."
          {...card("stdDev")}
          {...statusFor("stdDev")}
          description={`Standard deviation of the ${p("stdDev").long} of readings: how far readings typically are from the average.`}
        >
          <div className="insight-value">
            <span className="insight-value__big">
              &plusmn;{statsFor("stdDev").std_dev.toFixed(1)}
            </span>
            <span className="insight-value__unit">mmol/L</span>
          </div>
        </InsightCard>

        <InsightCard
          title="CV"
          {...card("cv")}
          {...statusFor("cv")}
          description="Coefficient of variation: standard deviation as a percentage of the mean. 36% or less is generally considered stable."
        >
          <div className="insight-value">
            <span className="insight-value__big">
              {statsFor("cv").cv.toFixed(0)}
            </span>
            <span className="insight-value__unit">% of mean</span>
          </div>
        </InsightCard>

        <InsightCard
          title="Flux"
          {...card("flux")}
          {...statusFor("flux")}
          description="Grade for glucose variability, from CV: A+ (≤20%), A (≤25%), B+ (≤30%), B (≤33%), C (≤36%), D (above 36%)."
        >
          <div className="insight-value">
            <span className="insight-value__big">
              {getFluxGrade(statsFor("flux").cv)}
            </span>
          </div>
        </InsightCard>
      </div>
    </div>
  );
}
