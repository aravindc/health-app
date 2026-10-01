import { useState, useEffect, useCallback } from "react";
import { api } from "./api/client";
import type {
  LastReading,
  DailyAvg,
  DailyTir,
  GmiResponse,
  InsightStats,
} from "./api/types";
import CurrentReading from "./components/CurrentReading";
import GlucoseInsulinChart from "./components/GlucoseInsulinChart";
import InsightsGrid from "./components/InsightsGrid";
import Heatmap from "./components/Heatmap";
import { avgColor, tirColor } from "./heatmapColors";
import "./App.css";

const REFRESH_INTERVAL = 60_000;
// The 90-day heatmaps and GMI move slowly, so they refresh less often (the
// same window as the Insights cards' longer periods, see useCachedFetch).
const SLOW_REFRESH_INTERVAL = 5 * 60_000;
const HEATMAP_DAYS = 90;

function App() {
  const [lastReading, setLastReading] = useState<LastReading | null>(null);
  const [stats24h, setStats24h] = useState<InsightStats | null>(null);
  const [gmi, setGmi] = useState<GmiResponse | null>(null);
  const [dailyAvg, setDailyAvg] = useState<DailyAvg[]>([]);
  const [dailyTir, setDailyTir] = useState<DailyTir[]>([]);
  const [error, setError] = useState<string | null>(null);
  // When the data was last refreshed (epoch ms). The chart uses it both to
  // refetch and, while following the latest data, as its window's end.
  const [refreshedAt, setRefreshedAt] = useState(() => Date.now());

  const fetchData = useCallback(async () => {
    try {
      const [lr, st] = await Promise.allSettled([
        api.getLastReading(),
        api.getInsightStats(24),
      ]);

      if (lr.status === "fulfilled") setLastReading(lr.value);
      if (st.status === "fulfilled") setStats24h(st.value);

      setError(null);
      setRefreshedAt(Date.now());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch data");
    }
  }, []);

  const fetchSlowData = useCallback(async () => {
    const [g, da, dt] = await Promise.allSettled([
      api.getGmi(90),
      api.getDailyAvg(HEATMAP_DAYS),
      api.getDailyTir(HEATMAP_DAYS),
    ]);
    if (g.status === "fulfilled") setGmi(g.value);
    if (da.status === "fulfilled") setDailyAvg(da.value);
    if (dt.status === "fulfilled") setDailyTir(dt.value);
  }, []);

  useEffect(() => {
    // fetchData's setState calls all happen inside its async body, after
    // awaiting Promise.allSettled — never synchronously during this effect
    // — so this isn't the render-cascade pattern react-hooks/set-state-in-effect
    // guards against; it's the standard fetch-on-mount-then-poll pattern.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchData();
    const id = setInterval(fetchData, REFRESH_INTERVAL);
    return () => clearInterval(id);
  }, [fetchData]);

  useEffect(() => {
    // Same fetch-on-mount-then-poll pattern as above.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchSlowData();
    const id = setInterval(fetchSlowData, SLOW_REFRESH_INTERVAL);
    return () => clearInterval(id);
  }, [fetchSlowData]);

  const avgHeatmapData = dailyAvg.map((d) => ({
    date: d.bg_date.slice(0, 10),
    value: d.bg_mmol,
  }));

  const tirHeatmapData = dailyTir.map((d) => ({
    date: d.bg_date.slice(0, 10),
    value: d.pir_strict,
    valueMedical: d.pir_medical,
  }));

  return (
    <div className="dashboard">
      <header className="dashboard__header">
        <CurrentReading reading={lastReading} />
        <div className="dashboard__labels">
          <span className="dashboard__label">CGM Data by Dexcom</span>
          <span className="dashboard__label">Insulin Data by Tandem</span>
        </div>
      </header>

      {error && <div className="dashboard__error">{error}</div>}

      <main className="dashboard__main">
        <GlucoseInsulinChart refreshedAt={refreshedAt} />

        <InsightsGrid
          stats24h={stats24h}
          gmi={gmi}
          refreshedAt={refreshedAt}
        />

        <div className="heatmaps">
          <Heatmap
            title="Daily Average"
            data={avgHeatmapData}
            colorFn={avgColor}
            labelFn={(v) => `${v.toFixed(1)} mmol/L`}
          />
          <Heatmap
            title="Daily Time in Range"
            data={tirHeatmapData}
            colorFn={tirColor}
            labelFn={(v) => `${v.toFixed(1)}%`}
          />
        </div>
      </main>
    </div>
  );
}

export default App;
