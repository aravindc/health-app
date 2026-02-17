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
import type { DataPoint } from "../api/types";

interface Props {
  data: DataPoint[];
  minMmol?: number;
  maxMmol?: number;
}

interface ChartPoint {
  time: number;
  mmol: number;
  color: string;
  label: string;
}

function formatHour(epoch: number): string {
  const d = new Date(epoch);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
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
  data,
  minMmol = 4.0,
  maxMmol = 10.0,
}: Props) {
  const chartData: ChartPoint[] = data.map((d) => ({
    time: d.epoch,
    mmol: d.mmol,
    color: d.point_color,
    label: d.time,
  }));

  return (
    <div className="bg-chart">
      <h2 className="bg-chart__title">Today</h2>
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
          <Tooltip
            content={<CustomTooltip />}
            cursor={false}
          />
          <Scatter
            data={chartData}
            shape={<CustomDot />}
          />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}
