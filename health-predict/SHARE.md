# Glucose prediction API: integration guide

This guide describes the prediction API's contract and how to draw its predictions on a chart. In health-app,
health-api implements it (`health-api/handlers/prediction.go`, `GET /prediction`) and the dashboard draws the
forecast.

> **Research use only.** The API predicts glucose values for research and analysis. It is not a medical device and
> must not be used to decide insulin doses or treatment. Show this disclaimer next to any predicted values. The
> API returns it in every response as `disclaimer`.

---

## 1. Where the API runs

| | |
|---|---|
| Base URL | `http://health-predict:8000` in Docker (internal `health_be` network only); `http://127.0.0.1:8000` with `make api` |
| Start it | `docker compose up -d health-predict` (health-app root), or `make api` here |
| Interactive docs / schema | `http://127.0.0.1:8000/docs`, `http://127.0.0.1:8000/openapi.json` |
| Authentication | none (internal network or localhost only) |
| CORS | **not enabled**. Call the API from your backend (server-side). A browser on another origin is blocked, so add a backend proxy route. |

Endpoints:

| Method | Path | Use |
|---|---|---|
| `POST` | `/predict` | predictions for one time `T` (the main call) |
| `GET` | `/health` | `{"status": "ok"}`, or 503 when the models can't be loaded |
| `GET` | `/model` | which model version is serving each horizon |

---

## 2. What to send

One request = one prediction time `T`, normally the time of the latest CGM reading. Send what was known at `T`:
recent CGM readings and recent insulin. **Don't send anything timestamped after `T`.** The API rejects it with 422.

All timestamps are ISO 8601 **with a timezone** (`2026-10-08T08:20:00Z` or `+01:00`). A timestamp without an offset
is rejected.

### Request body

```jsonc
{
  "timestamp": "2026-10-08T08:20:00Z",   // prediction time T (latest reading time)
  "glucose_unit": "mg/dL",               // or "mmol/L" (default). Applies to glucose and rate
  "glucose": 142,                         // optional: reading at T (can instead be the last item of recent_glucose)
  "recent_glucose": [                     // readings up to T, ideally the last 3 hours (~36 readings at 5 min)
    {"timestamp": "2026-10-08T08:15:00Z", "glucose": 139, "trend": 0, "rate": 0.3}
    // ...
  ],
  "recent_insulin": [                     // basal changes covering the last 5+ hours; boluses from the last 24 hours
    {"timestamp": "2026-10-08T02:00:00Z", "type": "basal_rate", "rate_u_per_h": 0.6, "profile_rate_u_per_h": 0.6},
    {"timestamp": "2026-10-08T07:05:00Z", "type": "bolus", "units": 4.5, "carbs_g": 45},
    {"timestamp": "2026-10-08T07:50:00Z", "type": "automated_correction", "units": 0.4}
  ]
}
```

### `recent_glucose[]`

| Field | Required | Notes |
|---|---|---|
| `timestamp` | yes | when the reading was measured |
| `glucose` | yes | in `glucose_unit`. Sensor LOW/HIGH: send the limit (40 / 400 mg/dL) |
| `trend` | recommended | −3 … +3 (see the mapping below). The model was trained with it, so leaving it out lowers accuracy slightly |
| `rate` | recommended | device rate of change in **`glucose_unit` per minute** |

Tandem CGM fields → API:

| Tandem `trend` code | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|
| API `trend` | +3 | +2 | +1 | 0 | −1 | −2 | −3 |

Tandem `rate` is in **0.1 mg/dL per minute**: send `rate / 10` with `glucose_unit: "mg/dL"`. Backfilled readings
are fine to include, but only once they have actually arrived by `T`.

### `recent_insulin[]`

| `type` | Fields | Meaning |
|---|---|---|
| `basal_rate` | `rate_u_per_h` (required), `profile_rate_u_per_h` (optional, the scheduled rate) | the rate in effect from `timestamp` until the next `basal_rate` event |
| `bolus` | `units`, optional `carbs_g` | user bolus (meal or manual). `timestamp` = when delivery **completed** |
| `manual_correction` | `units` | user correction without carbs |
| `automated_correction` | `units` | Control-IQ automatic correction |
| extended bolus | any bolus type + `duration_minutes`, `standard_percent` | `timestamp` = when it **started**; `standard_percent` = share delivered at once |

Tandem bolus mapping (from `event_properties`), the same mapping used in training:

| `bolusType` / `options` | API `type` |
|---|---|
| 2 / 3 | `automated_correction` |
| 3 / 4 with carbs | `bolus` |
| 3 / 4 without carbs | `manual_correction` |
| 1 (any options) | `bolus` (options 1 = extended: add `duration_minutes`, `standard_percent`) |

Include only completed boluses. Units are the units actually delivered.

**How much history to send:**
- **Glucose:** the last 3 hours (the model uses lags up to 180 min).
- **Basal:** every rate change in the last 5 hours, **plus the rate in effect 5 hours ago** (the event just before
  that window). Without it, the API can't tell the basal was known throughout and adds a warning.
- **Boluses:** the last 24 hours, and **always at least the most recent user bolus**, even if it is older.
  Time-since-last-bolus is an input.

---

## 3. What comes back

```jsonc
{
  "timestamp": "2026-10-08T08:20:00Z",
  "current_glucose": 7.88,                 // mmol/L, the reading the model used as "now"
  "unit": "mmol/L",                        // responses are ALWAYS mmol/L (×18.016 for mg/dL)
  "predictions": {
    "15m": {"value": 8.21, "lower": 7.55, "upper": 8.90, "lower_95": 7.10, "upper_95": 9.41, "confidence_level": 0.8},
    "30m": {"value": 8.47, "lower": 7.30, "upper": 9.70, "lower_95": 6.62, "upper_95": 10.55, "confidence_level": 0.8},
    "45m": { /* same shape */ },
    "60m": { /* same shape */ }
  },
  "model_version": {"15m": "random_forest/20261008-0623-911003b", "30m": "random_forest/…", "…": "…"},
  "warnings": ["…"],                       // show these. Empty list = inputs looked complete
  "disclaimer": "This system predicts glucose values for research and analysis. …"
}
```

- `value` is the point prediction for `T + 15 min` (and so on).
- `lower`/`upper` are the **80%** prediction interval. `lower_95`/`upper_95` are the 95% interval. In live
  checks, 80% intervals contain the actual value about 80% of the time.
- Predictions exist **only at 15, 30, 45 and 60 minutes**. For "next 30 minutes", use `15m` and `30m`.
- **Typical error** (mean absolute, recent live days): about 0.5 mmol/L (9 mg/dL) at 15 min and 0.8 mmol/L
  (15 mg/dL) at 30 min. Accuracy is lower around meals and fast changes.
- **Convert to mg/dL** if the chart uses it: `mg = mmol × 18.016`.

### Errors

| Status | When | Body |
|---|---|---|
| 422 | input problem: no timezone, a reading after `T`, glucose out of range, no reading in the 15 min before `T`, basal without a rate | `{"detail": "<message>"}`; pydantic schema errors have `detail` as a list |
| 503 | models not loaded | `{"detail": "models not available: …"}` |

When the latest reading is more than 15 minutes old, there is no prediction (422). Hide the forecast instead of
showing a stale one.

---

## 4. Drawing it on the chart

1. **When to call:** once per new CGM reading (every 5 min), with `timestamp` = that reading's time. Cache the
   response by `timestamp`. Repeated calls for the same `T` give the same answer.
2. **Line:** draw a dashed line from the current reading (`T`, `current_glucose`) to `T+15` (`15m.value`), then to
   `T+30` (`30m.value`). Points between those times are a visual interpolation, not predictions.
3. **Band:** shade `lower`→`upper` (80%) at `T`, `T+15` and `T+30`, starting at zero width at `T`. Optionally add a
   lighter 95% band.
4. **Style:** keep it visibly different from measured CGM (dashed or translucent, a "Predicted" legend entry) and
   don't extend thresholds or alarms onto it. Show `warnings` and the disclaimer near the chart (a tooltip is enough).
5. **History (optional):** to show how good past predictions were, store each response. Later, plot the 30-min
   prediction made at `T` against the reading that arrived at `T+30`.

### Example: TypeScript (server side)

```ts
type Interval = { value: number; lower: number; upper: number; lower_95: number; upper_95: number;
                  confidence_level: number };
type PredictResponse = { timestamp: string; current_glucose: number; unit: "mmol/L";
                         predictions: Record<"15m" | "30m" | "45m" | "60m", Interval>;
                         model_version: Record<string, string>; warnings: string[]; disclaimer: string };

const TANDEM_TREND: Record<number, number> = { 1: 3, 2: 2, 3: 1, 4: 0, 5: -1, 6: -2, 7: -3 };

export async function predictNext30(at: Date, cgm: { time: Date; mgdl: number; trend?: number; rawRate?: number }[],
                                    insulin: object[]): Promise<PredictResponse | null> {
  const since = at.getTime() - 3 * 3600_000;
  const body = {
    timestamp: at.toISOString(),
    glucose_unit: "mg/dL",
    recent_glucose: cgm
      .filter(r => r.time.getTime() >= since && r.time <= at)
      .map(r => ({ timestamp: r.time.toISOString(), glucose: r.mgdl,
                   trend: r.trend != null ? TANDEM_TREND[r.trend] ?? null : null,
                   rate: r.rawRate != null ? r.rawRate / 10 : null })),   // Tandem: 0.1 mg/dL/min
    recent_insulin: insulin,   // basal_rate / bolus / manual_correction / automated_correction events (section 2)
  };
  const res = await fetch("http://127.0.0.1:8000/predict", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (res.status === 422) return null;                 // e.g. stale or missing data: hide the forecast
  if (!res.ok) throw new Error(`prediction API ${res.status}`);
  return res.json();
}

// Points for the chart, in mg/dL: [T, T+15, T+30]
export function forecastSeries(p: PredictResponse) {
  const t = new Date(p.timestamp).getTime(), mg = (x: number) => x * 18.016;
  const pt = (min: number, v: number, lo: number, hi: number) =>
    ({ time: new Date(t + min * 60_000), value: mg(v), lower: mg(lo), upper: mg(hi) });
  return [
    pt(0, p.current_glucose, p.current_glucose, p.current_glucose),
    pt(15, p.predictions["15m"].value, p.predictions["15m"].lower, p.predictions["15m"].upper),
    pt(30, p.predictions["30m"].value, p.predictions["30m"].lower, p.predictions["30m"].upper),
  ];
}
```

### Quick test with curl

```sh
curl -s http://127.0.0.1:8000/health
curl -s -X POST http://127.0.0.1:8000/predict -H 'Content-Type: application/json' -d '{
  "timestamp": "2026-10-07T09:00:00Z", "glucose": 7.8,
  "recent_glucose": [{"timestamp": "2026-10-07T08:55:00Z", "glucose": 7.6},
                     {"timestamp": "2026-10-07T08:50:00Z", "glucose": 7.4}],
  "recent_insulin": [{"timestamp": "2026-10-07T04:00:00Z", "type": "basal_rate", "rate_u_per_h": 0.4},
                     {"timestamp": "2026-10-07T07:30:00Z", "type": "bolus", "units": 4.2, "carbs_g": 50}]
}'
```

This short example returns warnings about too little history. Real calls should send the full history described
in section 2.

---

## 5. Things to know

- **Model updates:** the served model changes occasionally after a manual promotion, and the 80%/95% ranges are
  recalibrated daily. The response shape doesn't change. `model_version` tells you which model produced a value,
  so store it if you keep history.
- **One person:** the model is trained on one person's data and is only meaningful for that person.
- **Carbs:** `carbs_g` is accepted, but the served model doesn't use carbs. Sending it is harmless.
- **No recommendations:** the API never suggests doses, corrections, basal changes or carbs, and the chart
  shouldn't present predictions that way.
