import { useState } from "react";

interface HeatmapDay {
  date: string; // ISO date string
  value: number;
  valueMedical?: number;
}

interface Props {
  title: string;
  data: HeatmapDay[];
  colorFn: (value: number, strict: boolean) => string;
  labelFn: (value: number) => string;
}

const DAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH_NAMES = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

function getWeekday(dateStr: string): number {
  return new Date(dateStr).getDay();
}

interface WeekColumn {
  weekStart: string;
  days: (HeatmapDay | null)[];
}

function buildWeeks(data: HeatmapDay[]): WeekColumn[] {
  if (data.length === 0) return [];

  const sorted = [...data].sort(
    (a, b) => new Date(a.date).getTime() - new Date(b.date).getTime()
  );

  const weeks: WeekColumn[] = [];
  let currentWeek: (HeatmapDay | null)[] = new Array(7).fill(null);
  let currentWeekStart = "";

  for (const day of sorted) {
    const dow = getWeekday(day.date);

    if (dow === 0 && currentWeekStart !== "" && currentWeek.some((d) => d !== null)) {
      weeks.push({ weekStart: currentWeekStart, days: currentWeek });
      currentWeek = new Array(7).fill(null);
    }

    if (dow === 0 || currentWeekStart === "") {
      currentWeekStart = day.date;
    }

    currentWeek[dow] = day;
  }

  if (currentWeek.some((d) => d !== null)) {
    weeks.push({ weekStart: currentWeekStart, days: currentWeek });
  }

  return weeks;
}

function getMonthLabels(weeks: WeekColumn[]): { label: string; col: number }[] {
  const labels: { label: string; col: number }[] = [];
  let lastMonth = -1;

  for (let i = 0; i < weeks.length; i++) {
    const firstDay = weeks[i].days.find((d) => d !== null);
    if (!firstDay) continue;
    const month = new Date(firstDay.date).getMonth();
    if (month !== lastMonth) {
      labels.push({ label: MONTH_NAMES[month], col: i });
      lastMonth = month;
    }
  }

  return labels;
}

export default function Heatmap({ title, data, colorFn, labelFn }: Props) {
  const [strict, setStrict] = useState(true);
  const weeks = buildWeeks(data);
  const monthLabels = getMonthLabels(weeks);

  const cellSize = 14;
  const cellGap = 3;
  const step = cellSize + cellGap;
  const labelWidth = 60;
  const headerHeight = 24;

  const svgWidth = labelWidth + weeks.length * step + 10;
  const svgHeight = headerHeight + 7 * step + 5;

  return (
    <div className="heatmap-card">
      <h3 className="heatmap-card__title">{title}</h3>
      <button
        className={`heatmap-card__toggle ${strict ? "heatmap-card__toggle--active" : ""}`}
        onClick={() => setStrict(!strict)}
      >
        {strict ? "Strict (4.0 - 7.0)" : "Medical (4.0 - 10.0)"}
      </button>
      <div className="heatmap-card__scroll">
        <svg
          width={svgWidth}
          height={svgHeight}
          className="heatmap-svg"
        >
          {/* Month labels */}
          {monthLabels.map((m) => (
            <text
              key={`${m.label}-${m.col}`}
              x={labelWidth + m.col * step}
              y={14}
              className="heatmap-month"
            >
              {m.label}
            </text>
          ))}

          {/* Day-of-week labels */}
          {[0, 2, 4, 6].map((dow) => (
            <text
              key={dow}
              x={labelWidth - 8}
              y={headerHeight + dow * step + cellSize - 2}
              className="heatmap-day-label"
              textAnchor="end"
            >
              {DAY_LABELS[dow]}
            </text>
          ))}

          {/* Cells */}
          {weeks.map((week, wi) =>
            week.days.map((day, dow) => {
              if (!day) return null;
              const x = labelWidth + wi * step;
              const y = headerHeight + dow * step;
              return (
                <g key={`${wi}-${dow}`}>
                  <rect
                    x={x}
                    y={y}
                    width={cellSize}
                    height={cellSize}
                    rx={3}
                    fill={colorFn(!strict && day.valueMedical !== undefined ? day.valueMedical : day.value, strict)}
                  />
                  <title>
                    {day.date}: {labelFn(!strict && day.valueMedical !== undefined ? day.valueMedical : day.value)}
                  </title>
                </g>
              );
            })
          )}
        </svg>
      </div>
    </div>
  );
}

// Color functions for Daily Average heatmap
export function avgColor(mmol: number, strict: boolean): string {
  const low = 4.0;
  const high = strict ? 7.0 : 10.0;

  if (mmol < low) return "#c0392b";       // red — low
  if (mmol <= high) return "#27ae60";        // green — in range
  if (mmol <= high * 1.1) return "#e67e22";  // orange — slightly above
  if (mmol <= high * 1.2) return "#d35400"; // dark orange — above
  return "#c0392b";                         // red — high
}

// Color function for TIR heatmap
export function tirColor(pir: number, _strict: boolean): string {
  if (pir >= 85) return "#196f3d";   // dark green
  if (pir >= 70) return "#27ae60";   // green
  if (pir >= 55) return "#f1c40f";   // yellow
  if (pir >= 40) return "#e67e22";   // orange
  if (pir >= 25) return "#d35400";   // dark orange
  return "#c0392b";                  // red
}
