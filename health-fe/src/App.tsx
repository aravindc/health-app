import { useState, useEffect, useCallback, useRef } from "react";
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

// Polling, while the tab is visible: /lastreading is checked every minute
// (it keeps the dial and its "N min ago" current), and everything else is
// refetched only when that shows a new CGM reading, since nothing else
// changes between readings (every 5 minutes). A full refresh also runs if
// MAX_REFRESH_GAP passes without one, so pump data (which arrives
// separately, from Tandem) still shows up when the CGM is quiet or offline.
const POLL_INTERVAL = 60_000;
const MAX_REFRESH_GAP = 5 * 60_000;
// The 90-day heatmaps and GMI move slowly: at most one refresh per this
// interval (the same window as the Insights cards' longer periods, see
// useCachedFetch).
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
  // bg_time of the newest reading seen, and when the last full and slow
  // refreshes ran (epoch ms; 0 = never).
  const lastSeenReading = useRef<number | null>(null);
  const lastFullRefresh = useRef(0);
  const lastSlowRefresh = useRef(0);

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

  const poll = useCallback(async () => {
    try {
      const [lr] = await Promise.allSettled([api.getLastReading()]);
      const reading = lr.status === "fulfilled" ? lr.value : null;
      if (reading) setLastReading(reading);

      const now = Date.now();
      const isNewReading = reading !== null && reading.bg_time !== lastSeenReading.current;
      if (!isNewReading && now - lastFullRefresh.current < MAX_REFRESH_GAP) return;
      if (reading) lastSeenReading.current = reading.bg_time;
      lastFullRefresh.current = now;

      if (now - lastSlowRefresh.current >= SLOW_REFRESH_INTERVAL) {
        lastSlowRefresh.current = now;
        fetchSlowData();
      }
      const [st] = await Promise.allSettled([api.getInsightStats(24)]);
      if (st.status === "fulfilled") setStats24h(st.value);

      setError(null);
      setRefreshedAt(now);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch data");
    }
  }, [fetchSlowData]);

  useEffect(() => {
    // Poll only while the tab is visible; on becoming visible, check at
    // once rather than waiting up to a minute. poll's setState calls all
    // happen after an await, never synchronously in this effect, so this is
    // the fetch-on-mount-then-poll pattern, not a render cascade.
    let id: ReturnType<typeof setInterval> | undefined;
    const start = () => {
      if (id !== undefined) return;
      poll();
      id = setInterval(poll, POLL_INTERVAL);
    };
    const stop = () => {
      clearInterval(id);
      id = undefined;
    };
    const onVisibilityChange = () => (document.hidden ? stop() : start());
    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [poll]);

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
