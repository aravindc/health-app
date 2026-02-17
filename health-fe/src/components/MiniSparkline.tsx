import { ResponsiveContainer, LineChart, Line, YAxis } from "recharts";
import type { SparklinePoint } from "../api/types";

interface Props {
  data: SparklinePoint[];
}

export default function MiniSparkline({ data }: Props) {
  if (data.length === 0) return <div className="mini-sparkline">--</div>;

  const sorted = [...data].sort((a, b) => a.bg_time - b.bg_time);

  return (
    <div className="mini-sparkline">
      <ResponsiveContainer width="100%" height={50}>
        <LineChart data={sorted}>
          <YAxis domain={[2, 16]} hide />
          <Line
            type="monotone"
            dataKey="bg_mmol"
            stroke="#e74c7a"
            strokeWidth={1.5}
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
