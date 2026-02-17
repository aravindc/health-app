import type {
  LastReading,
  SparklinePoint,
  DataPoint,
  DailyAvg,
  AvgMmol,
  QuartPoint,
  GmiResponse,
  PercentInRange,
  DailyTir,
} from "./types";

const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:9082";

async function fetchJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export const api = {
  getLastReading: () => fetchJSON<LastReading>("/lastreading"),
  getLast24hSparkline: () => fetchJSON<SparklinePoint[]>("/last24hsparkline"),
  getLastXh: (hours: number) => fetchJSON<DataPoint[]>(`/lastxh/${hours}`),
  getAvgMmol: (period: string) => fetchJSON<AvgMmol>(`/avgmmol/${period}`),
  getQuart: (days: number) => fetchJSON<QuartPoint[]>(`/quart/${days}`),
  getGmi: (days: number) => fetchJSON<GmiResponse>(`/gmi/${days}`),
  getPercentInRange: (hours: number) =>
    fetchJSON<PercentInRange[]>(`/percentinrange/${hours}`),
  getDailyAvg: (days: number) => fetchJSON<DailyAvg[]>(`/dailyavg/${days}`),
  getDailyTir: (days: number) => fetchJSON<DailyTir[]>(`/dailytir/${days}`),
};
