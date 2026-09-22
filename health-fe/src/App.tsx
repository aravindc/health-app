import { useState, useEffect, useCallback } from "react";
import { api } from "./api/client";
import type {
  LastReading,
  SparklinePoint,
  DataPoint,
  DailyAvg,
  DailyTir,
  AvgMmol,
  QuartPoint,
  GmiResponse,
  PercentInRange,
} from "./api/types";
import CurrentReading from "./components/CurrentReading";
import GlucoseInsulinChart from "./components/GlucoseInsulinChart";
import InsightsGrid from "./components/InsightsGrid";
import Heatmap from "./components/Heatmap";
import { avgColor, tirColor } from "./heatmapColors";
import "./App.css";

const REFRESH_INTERVAL = 60_000;
const HEATMAP_DAYS = 120;

function App() {
  const [lastReading, setLastReading] = useState<LastReading | null>(null);
  const [sparkline24h, setSparkline24h] = useState<SparklinePoint[]>([]);
  const [dataPoints24h, setDataPoints24h] = useState<DataPoint[]>([]);
  const [avgMmol24h, setAvgMmol24h] = useState<AvgMmol | null>(null);
  const [quartiles, setQuartiles] = useState<QuartPoint[]>([]);
  const [gmi, setGmi] = useState<GmiResponse | null>(null);
  const [percentInRange, setPercentInRange] = useState<PercentInRange[]>([]);
  const [dailyAvg, setDailyAvg] = useState<DailyAvg[]>([]);
  const [dailyTir, setDailyTir] = useState<DailyTir[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [chartRefreshTick, setChartRefreshTick] = useState(0);

  const fetchData = useCallback(async () => {
    try {
      const [lr, sp, dp, avg, q, g, pir, da, dt] = await Promise.allSettled([
        api.getLastReading(),
        api.getLast24hSparkline(),
        api.getLastXh(24),
        api.getAvgMmol("1d"),
        api.getQuart(1),
        api.getGmi(90),
        api.getPercentInRange(24),
        api.getDailyAvg(HEATMAP_DAYS),
        api.getDailyTir(HEATMAP_DAYS),
      ]);

      if (lr.status === "fulfilled") setLastReading(lr.value);
      if (sp.status === "fulfilled") setSparkline24h(sp.value);
      if (dp.status === "fulfilled") setDataPoints24h(dp.value);
      if (avg.status === "fulfilled") setAvgMmol24h(avg.value);
      if (q.status === "fulfilled") setQuartiles(q.value);
      if (g.status === "fulfilled") setGmi(g.value);
      if (pir.status === "fulfilled") setPercentInRange(pir.value);
      if (da.status === "fulfilled") setDailyAvg(da.value);
      if (dt.status === "fulfilled") setDailyTir(dt.value);

      setError(null);
      setChartRefreshTick((t) => t + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch data");
    }
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
        <span className="dashboard__label">CGM Data by Dexcom</span>
      </header>

      {error && <div className="dashboard__error">{error}</div>}

      <main className="dashboard__main">
        <GlucoseInsulinChart refreshTick={chartRefreshTick} />

        <InsightsGrid
          avgMmol24h={avgMmol24h}
          quartiles={quartiles}
          gmi={gmi}
          percentInRange={percentInRange}
          sparkline24h={sparkline24h}
          dataPoints24h={dataPoints24h}
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
