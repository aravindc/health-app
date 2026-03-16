import { useState, useEffect, useCallback } from "react";
import {
  ResponsiveContainer,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  ReferenceLine,
  Tooltip,
  CartesianGrid,
} from "recharts";
import { api } from "../api/client";
import type { DataPoint } from "../api/types";

interface Props {
  minMmol?: number;
  maxMmol?: number;
  refreshTick?: number; // increment to trigger a refresh of the current window
}

interface ChartPoint {
  time: number;
  mmol: number;
  color: string;
  label: string;
}

/** Returns a YYYY-MM-DD string for today minus `daysBack` days. */
function dateForOffset(daysBack: number): string {
  const d = new Date();
  d.setDate(d.getDate() - daysBack);
  return d.toISOString().slice(0, 10);
}

function formatHour(epoch: number): string {
  const d = new Date(epoch);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function windowLabel(daysBack: number): string {
  if (daysBack === 0) return "Today";
  const d = new Date();
  d.setDate(d.getDate() - daysBack);
  return d.toLocaleDateString([], {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

function CustomDot(props: Record<string, unknown>) {
  const { cx, cy, payload } = props as {
    cx: number;
    cy: number;
    payload: ChartPoint;
  };
  return <circle cx={cx} cy={cy} r={3} fill={payload.color} />;
}

function CustomTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: { payload: ChartPoint }[];
}) {
  if (!active || !payload?.length) return null;
  const pt = payload[0].payload;
  return (
    <div className="chart-tooltip">
      <div>{pt.mmol.toFixed(1)} mmol/L</div>
      <div>{pt.label}</div>
    </div>
  );
}

export default function BgChart({
  minMmol = 4.0,
  maxMmol = 10.0,
  refreshTick = 0,
}: Props) {
  const [daysBack, setDaysBack] = useState(0);
  const [data, setData] = useState<DataPoint[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchWindow = useCallback(async (days: number) => {
    setLoading(true);
    try {
      const points = await api.getDayChart(dateForOffset(days));
      setData(points);
    } finally {
      setLoading(false);
    }
  }, []);

  // Refetch when day changes or parent triggers a refresh (tick changes)
  useEffect(() => {
    fetchWindow(daysBack);
  }, [daysBack, refreshTick, fetchWindow]);

  const goBack = () => setDaysBack((d) => d + 1);
  const goForward = () => setDaysBack((d) => Math.max(0, d - 1));

  const chartData: ChartPoint[] = data.map((d) => ({
    time: d.epoch,
    mmol: d.mmol,
    color: d.point_color,
    label: d.time,
  }));

  return (
    <div className="bg-chart">
      <div className="bg-chart__header">
        <button
          className="bg-chart__nav"
          onClick={goBack}
          title="Previous 24 hours"
        >
          ‹
        </button>
        <h2 className="bg-chart__title">
          {windowLabel(daysBack)}
          {loading && <span className="bg-chart__loading"> …</span>}
        </h2>
        <button
          className="bg-chart__nav"
          onClick={goForward}
          disabled={daysBack === 0}
          title="Next 24 hours"
        >
          ›
        </button>
      </div>
      <ResponsiveContainer width="100%" height={300}>
        <ScatterChart margin={{ top: 10, right: 20, bottom: 10, left: 10 }}>
          <CartesianGrid
            strokeDasharray="3 3"
            stroke="rgba(255,255,255,0.06)"
          />
          <XAxis
            dataKey="time"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={formatHour}
            stroke="#888"
            tick={{ fill: "#888", fontSize: 12 }}
          />
          <YAxis
            dataKey="mmol"
            domain={[0, 20]}
            stroke="#888"
            tick={{ fill: "#888", fontSize: 12 }}
            unit=" "
          />
          <ReferenceLine
            y={minMmol}
            stroke="#e74c3c"
            strokeDasharray="4 4"
            strokeWidth={1.5}
          />
          <ReferenceLine
            y={maxMmol}
            stroke="#e67e22"
            strokeDasharray="4 4"
            strokeWidth={1.5}
          />
          <Tooltip content={<CustomTooltip />} cursor={false} />
          <Scatter data={chartData} shape={<CustomDot />} />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}
