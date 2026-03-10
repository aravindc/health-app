import InsightCard from "./InsightCard";
import MiniSparkline from "./MiniSparkline";
import type {
  AvgMmol,
  QuartPoint,
  GmiResponse,
  PercentInRange,
  SparklinePoint,
  DataPoint,
} from "../api/types";

interface Props {
  avgMmol24h: AvgMmol | null;
  quartiles: QuartPoint[];
  gmi: GmiResponse | null;
  percentInRange: PercentInRange[];
  sparkline24h: SparklinePoint[];
  dataPoints24h: DataPoint[];
}

function computeStats(points: DataPoint[]) {
  if (points.length === 0) return { median: 0, stdDev: 0, cv: 0, count: 0 };
  const values = points.map((p) => p.mmol).sort((a, b) => a - b);
  const n = values.length;
  const median =
    n % 2 === 0
      ? (values[n / 2 - 1] + values[n / 2]) / 2
      : values[Math.floor(n / 2)];
  const mean = values.reduce((s, v) => s + v, 0) / n;
  const variance = values.reduce((s, v) => s + (v - mean) ** 2, 0) / n;
  const stdDev = Math.sqrt(variance);
  const cv = mean > 0 ? (stdDev / mean) * 100 : 0;
  return { median, stdDev, cv, count: n };
}

function getQuartileSummary(points: QuartPoint[]) {
  if (points.length === 0) return { q1: 0, q2: 0, q3: 0 };
  const values = points.map((p) => p.value).sort((a, b) => a - b);
  const n = values.length;
  const q1 = values[Math.floor(n * 0.25)] ?? 0;
  const q2 = values[Math.floor(n * 0.5)] ?? 0;
  const q3 = values[Math.floor(n * 0.75)] ?? 0;
  return { q1, q2, q3 };
}

function countHighsLows(points: DataPoint[], maxMmol: number) {
  let highs = 0;
  let lows = 0;
  for (const p of points) {
    if (p.mmol > maxMmol) highs++;
    else if (p.mmol < 4.0) lows++;
  }
  return { highs, lows };
}

function getFluxGrade(cv: number): string {
  if (cv <= 20) return "A+";
  if (cv <= 25) return "A";
  if (cv <= 30) return "B+";
  if (cv <= 33) return "B";
  if (cv <= 36) return "C";
  return "D";
}

export default function InsightsGrid({
  avgMmol24h,
  quartiles,
  gmi,
  percentInRange,
  sparkline24h,
  dataPoints24h,
}: Props) {
  const stats = computeStats(dataPoints24h);
  const quarts = getQuartileSummary(quartiles);
  const { highs, lows } = countHighsLows(dataPoints24h, 10.0);
  const { highs: highs7, lows: lows7 } = countHighsLows(dataPoints24h, 7.0);

  const pirStrict = percentInRange?.[0]?.data?.[0]?.y ?? 0;

  // Count unicorns (perfect 5-minute readings in range 4.0–7.0)
  const unicorns = dataPoints24h.filter(
    (p) => p.mmol >= 4.0 && p.mmol <= 7.0
  ).length;

  // Low/High/InRange breakdown (4–10 mmol)
  const totalPts = dataPoints24h.length || 1;
  const lowPct = Math.round((lows / totalPts) * 100);
  const highPct = Math.round((highs / totalPts) * 100);
  const inRangePct = 100 - lowPct - highPct;

  // Strict breakdown (4–7 mmol)
  const lowPct7 = Math.round((lows7 / totalPts) * 100);
  const highPct7 = Math.round((highs7 / totalPts) * 100);
  const inRangePct7 = 100 - lowPct7 - highPct7;

  return (
    <div className="insights">
      <h2 className="insights__title">Insights</h2>
      <div className="insights__grid">
        <InsightCard title="% In Range" period="24 hours">
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
              <text x="18" y="20" className="ring-text">
                {Math.round(pirStrict)}
              </text>
            </svg>
          </div>
        </InsightCard>

        <InsightCard title="Average Glucose" period="24 hours">
          <div className="insight-value">
            <span className="insight-value__big">
              {avgMmol24h?.bg_mmol?.toFixed(1) ?? "--"}
            </span>
            <span className="insight-value__unit">mmol/L</span>
          </div>
        </InsightCard>

        <InsightCard title="Mini Graph" period="24 hours">
          <MiniSparkline data={sparkline24h} />
        </InsightCard>

        <InsightCard title="Quartiles" period="24 hours">
          <div className="quartile-display">
            <span className="quartile-val">{quarts.q1.toFixed(1)}</span>
            <span className="quartile-val quartile-val--mid">
              {quarts.q2.toFixed(1)}
            </span>
            <span className="quartile-val">{quarts.q3.toFixed(1)}</span>
          </div>
        </InsightCard>

        <InsightCard title="Normal Range %" period="24 hours">
          <div className="range-bar">
            <span className="range-bar__low" style={{ flex: lowPct }}>
              {lowPct}%
            </span>
            <span className="range-bar__in" style={{ flex: inRangePct }}>
              {inRangePct}%
            </span>
            <span className="range-bar__high" style={{ flex: highPct }}>
              {highPct}%
            </span>
          </div>
        </InsightCard>

        <InsightCard title="Strict Range %" period="24 hours (4–7)">
          <div className="range-bar">
            <span className="range-bar__low" style={{ flex: lowPct7 }}>
              {lowPct7}%
            </span>
            <span className="range-bar__in" style={{ flex: inRangePct7 }}>
              {inRangePct7}%
            </span>
            <span className="range-bar__high" style={{ flex: highPct7 }}>
              {highPct7}%
            </span>
          </div>
        </InsightCard>

        <InsightCard title="GMI" period="90 days">
          <div className="insight-value">
            <span className="insight-value__big">
              {gmi?.gmi_percent?.toFixed(1) ?? "--"}
            </span>
            <span className="insight-value__unit">%</span>
          </div>
        </InsightCard>

        <InsightCard title="Unicorns" period="24 hours">
          <div className="insight-value">
            <span className="insight-value__big">{unicorns}</span>
            <span className="insight-value__unit">found</span>
          </div>
        </InsightCard>

        <InsightCard title="Highs / Lows" period="24 hours">
          <div className="insight-value">
            <span className="insight-value__big">
              {highs} / {lows}
            </span>
          </div>
        </InsightCard>

        <InsightCard title="Median" period="24 hours">
          <div className="insight-value">
            <span className="insight-value__big">
              {stats.median.toFixed(1)}
            </span>
            <span className="insight-value__unit">mmol/L</span>
          </div>
        </InsightCard>

        <InsightCard title="Std. Dev." period="24 hours">
          <div className="insight-value">
            <span className="insight-value__big">
              &plusmn;{stats.stdDev.toFixed(1)}
            </span>
            <span className="insight-value__unit">mmol/L</span>
          </div>
        </InsightCard>

        <InsightCard title="CV" period="24 hours">
          <div className="insight-value">
            <span className="insight-value__big">
              {stats.cv.toFixed(0)}
            </span>
            <span className="insight-value__unit">% of mean</span>
          </div>
        </InsightCard>

        <InsightCard title="Flux" period="24 hours">
          <div className="insight-value">
            <span className="insight-value__big">
              {getFluxGrade(stats.cv)}
            </span>
          </div>
        </InsightCard>
      </div>
    </div>
  );
}
