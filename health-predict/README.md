# T1D glucose prediction (research)

Part of [health-app](../README.md): health-api calls this project's prediction API (the `health-predict` service)
and the dashboard shows the next 30 minutes of predicted glucose. It was developed as a separate project
(t1danalysis) and moved here.

> **This system predicts glucose values for research and analysis. It is not a medical device and must not be used
> to determine insulin doses or make treatment decisions.**
>
> It makes no insulin dosing, correction, basal or carbohydrate recommendations and no diagnosis, and its
> predictions are not medically accurate. Every API response repeats this statement.

The system predicts one person's glucose 15, 30, 45 and 60 minutes ahead from CGM and insulin-pump history, with
prediction intervals. It answers one question: *given everything known at time T, how accurately can this
person's glucose at T+15 … T+60 be predicted?* Every model is compared with simply assuming glucose stays where it
is ("persistence").

## Results

All numbers come from the held-out test period (23 Jul – 6 Oct 2026). It was used once, after the model had been
chosen on validation data.

| MAE, mmol/L | 15 min | 30 min | 45 min | 60 min |
|---|---|---|---|---|
| Persistence (glucose stays the same) | 0.686 | 1.075 | 1.407 | 1.690 |
| Linear regression | 0.607 | 0.928 | 1.207 | 1.446 |
| **Random forest (selected)** | **0.562** | **0.853** | **1.114** | **1.355** |
| LightGBM | 0.571 | 0.873 | 1.142 | 1.388 |
| TCN (sequence model) | 0.570 | 0.861 | 1.120 | 1.351 |

- **Accuracy:** the selected model is 18–21% better than persistence, matching validation (18–23%).
- **Intervals:** the conformal prediction intervals are calibrated. 80% intervals contained the actual value
  79–82% of the time and 95% intervals 95–96%, in every glucose range.
- **Lows:** the models rank low risk well (ROC-AUC 0.94), but the alert cutoff matters. Requiring a predicted
  value below 3.9 catches only 8% of lows about to start within 30 minutes. A cutoff chosen on validation months
  (10% quantile model below ≈ 4.25 mmol/L) caught 85% on the test period at 27% precision, with about 2.7 false
  alert episodes a day and roughly 27 minutes of warning ([`reports/lows_report.md`](reports/lows_report.md)).
  This is analysis, not an alarm system.

Details: [`reports/test_report.md`](reports/test_report.md) (test period),
[`reports/model_comparison.md`](reports/model_comparison.md) (model selection),
[`reports/feature_report.md`](reports/feature_report.md) (features and leakage),
[`reports/data_quality_report.md`](reports/data_quality_report.md) (data). Reports are generated, not committed
(they summarise personal health data); `make phase5` rebuilds them.

## Quick start

```sh
make install          # Python 3.12 virtualenv via uv (ML, deep learning and API extras)
make phase1           # ingest from Postgres -> DuckDB, data-quality report
make features         # 5-min grid, features, targets, splits, leakage audit
make phase3 phase4    # cross-validate all models (~50 min), comparison report
make final            # fit the selected model on all development data -> new candidate version
make holdout          # ONE-TIME test-period evaluation (refuses a second run)
make analyze          # intervals, SHAP, error analysis -> reports/test_report.md
make api              # prediction API on http://127.0.0.1:8000 (interactive docs at /docs)
make dashboard        # interactive offline dashboard -> reports/dashboard.html
make test             # all tests
```

`make help` lists everything. The pipeline reads one of two Postgres databases, chosen by `PREDICT_DB` in `.env`
(see `.env.example`):

- **`health-db`** (default): health-app's own database, with the `POSTGRES_*` settings from health-app's root
  `.env`. From the host, `PREDICT_DB_HOST=127.0.0.1` and `PREDICT_DB_PORT=9084` reach the container. Use this where
  `health-db` holds the full history, as on the server.
- **`replica`**: a copy of another `health-db` (e.g. the server's) that [`dbsync/`](dbsync/README.md) keeps current,
  in the `health-predict-replica` service (`docker compose --profile predict-replica up -d` from health-app's root,
  credentials in `replica.env`). Use this to train on this machine.

Ingest copies rows by id, and ids differ between databases, so an existing snapshot refuses to switch databases
until you re-copy everything with `python -m src.ingest --full`. Credentials are never logged, and the Postgres
session is read-only.

**GPU (optional):** the LSTM and TCN use CUDA when it's available. On WSL the driver library is in
`/usr/lib/wsl/lib`, which the Makefile adds to `LD_LIBRARY_PATH`. Without a GPU everything runs on CPU.

## How it works

```
Postgres: health-db, or a replica of it (PREDICT_DB)                dbsync keeps a replica current
   │  src/ingest.py        raw snapshot (unchanged) -> cleaning -> dq_issues      [DuckDB data/processed/t1d.duckdb]
   │  src/validate_data.py data-quality report
   ▼
5-minute grid ── src/features.py   102 leakage-safe features (as_of rebuildable), src/iob.py estimated IOB
               ├ src/targets.py    glucose_{h}m, delta_{h}m
               └ src/splits.py     fixed test period + monthly rolling-origin folds, 60-min embargo
   ▼
src/train.py --cv      persistence, linear trend, linear, random forest, XGBoost, LightGBM, LSTM, TCN
src/evaluate.py        comparison + selection rule (complex model only if ≥5% better in ≥75% of months)
   ▼
src/train.py --final   candidate version -> models/<model>/<h>m/<version>/  (+ conformal.json)
src/holdout.py         fixed test period: once for selection; --candidate for later versions
src/registry.py        promotion gate -> models/registry.json
   ▼
src/serving.py ── src/api.py (POST /predict) · src/predict.py (CLI) · uses the SAME feature code as training
```

### Data
- **Main CGM source:** Tandem pump CGM, in mg/dL, converted to mmol/L. Nightscout readings fill its gaps. The
  modelling window is 19 May 2025 – 6 Oct 2026: about 506 days with both CGM and insulin data.
- **Backfilled CGM readings** are recorded at arrival time, on average 22 minutes late. Their measurement time is
  rebuilt from the pump clock. Each reading keeps both `measured_at` and `available_at`.
- **Insulin:** basal comes from the pump's rate-change log, integrated over time. Boluses are mapped in
  `config/mappings.yaml` to `bolus`, `automated_correction` (Control-IQ) and `manual_correction`.
- **Re-entered meals:** when a bolus is cancelled and re-entered, its carbs are counted once.
- **Insulin gaps:** basal gaps longer than 6 hours, such as pump-off periods with unrecorded injections, mean
  insulin is *unknown*, not zero. Those samples are left out of training and evaluation.
- **Nothing is deleted:** every flag and exclusion is recorded in the `dq_issues` table.

### Leakage prevention
These rules are enforced in code and tested in `tests/test_leakage.py`. An audit rebuilds features from only what
was known at chosen times and compares them with the normal build.
- A CGM reading is used at T only when `available_at <= T`. Late backfills aren't used before they arrived.
- No interpolation. A lag value may fall back to an older reading, never a newer one. All windows are trailing.
- Insulin is modelled as a real-time stream. A standard bolus counts from completion. An extended bolus runs at its
  programmed rate and stops early only once a cancellation has actually happened.
- Day of year is excluded from models. With about one year of history it acts as a hidden date index and made the
  models memorise periods.
- Scaling, imputation, clipping and interval calibration are all fitted on training data only.

### Splits
- **Test period:** fixed at 23 Jul 2026 onward and used once for the selection decision.
- **Validation:** 8 rolling-origin folds. Each validates on one month (Dec 2025 – Jul 2026) and trains on everything
  before it.
- **Embargo:** a 60-minute gap before every validation and test period, so no training target falls inside it.
- No random splits anywhere.

### Models and selection
- **Hyperparameters** are defaults in `config/models.yaml`. Nested tuning (`python -m src.tune`) didn't beat them
  enough to matter.
- **Selection rule:** a more complex model is adopted only if it lowers MAE by ≥ 5% *and* wins in ≥ 75% of
  validation months.
- **Outcome:** random forest, predicting the glucose change, was selected at every horizon. XGBoost, LightGBM, the
  LSTM and the TCN came within 0.5–3% of it but didn't beat it.
- **Feature effects:** insulin features improve accuracy by 4–7%. Estimated IOB adds nothing beyond them, and carbs
  add about 1% at longer horizons.
- **Older history:** 3.5 years of earlier glucose-only data (Nightscout) helps glucose-only models by 1–2% but makes
  the insulin model 1–2% worse (a different therapy period), so it isn't used
  ([`reports/pretraining_report.md`](reports/pretraining_report.md)).

### Prediction intervals
- **Method:** Mondrian split conformal. Out-of-fold validation errors are grouped by current glucose range ×
  direction of change, and each group's error quantiles become its interval offsets.
- **Storage:** each model version stores its calibration in `conformal.json`.
- **Comparison:** LightGBM quantile regression was tried as well, and its intervals were too narrow (67–74%
  coverage at 80%).

## Prediction API

`POST /predict` (interactive docs at `/docs` when the API is running):

```json
{
  "timestamp": "2026-10-07T09:00:00Z",
  "glucose": 7.8,
  "glucose_unit": "mmol/L",
  "recent_glucose": [{"timestamp": "2026-10-07T08:55:00Z", "glucose": 7.6, "trend": 0, "rate": 0.02}],
  "recent_insulin": [
    {"timestamp": "2026-10-07T04:00:00Z", "type": "basal_rate", "rate_u_per_h": 0.4, "profile_rate_u_per_h": 0.4},
    {"timestamp": "2026-10-07T07:30:00Z", "type": "bolus", "units": 4.2},
    {"timestamp": "2026-10-07T08:10:00Z", "type": "bolus", "units": 3.0, "duration_minutes": 60, "standard_percent": 40}
  ]
}
```

Response (values illustrative):

```json
{
  "timestamp": "2026-10-07T09:00:00Z", "current_glucose": 7.8, "unit": "mmol/L",
  "predictions": {"30m": {"value": 8.6, "lower": 7.3, "upper": 10.0, "lower_95": 6.4, "upper_95": 11.3,
                          "confidence_level": 0.8}, "...": "15m, 45m, 60m"},
  "model_version": {"30m": "random_forest/20261008-0623-911003b"},
  "warnings": [],
  "disclaimer": "This system predicts glucose values for research and analysis. It is not a medical device ..."
}
```

What to send:
- **Glucose:** readings in mmol/L, or set `glucose_unit: "mg/dL"`. Ideally send the last 3+ hours; `trend` and
  `rate` (the device's own values) are optional but the model uses them.
- **Basal:** rate changes covering the last 5+ hours.
- **Boluses:** the last 24 hours, and at least the most recent bolus however old, because time since the last bolus
  is an important input.
- **Timestamps** need a timezone (ISO 8601).

Responses:
- **Inputs it can't predict from** (no reading in the last 15 minutes, values out of range, future timestamps)
  return HTTP 422.
- **Inputs outside the conditions the model was validated on** still return a prediction, plus a `warnings` entry.
  Examples: incomplete basal history, no recent bolus, a sensor reading at its LOW limit.

The API uses exactly the same feature code as training. A parity check found identical model inputs at 60 test
times, with predictions matching the recorded test predictions to within 0.005 mmol/L.

Also available:
- `GET /model` — the production versions.
- `GET /health` — service status.
- `python -m src.predict --at <time>` — predicts from the local database as of that time.

Docker: `make docker-api` starts the `health-predict` service from health-app's `docker-compose.yml`. The image holds
code only. Models are mounted read-only, and no port is published: health-api reaches it
at `http://health-predict:8000` on the internal `health_be` network.

## Dashboard

`make dashboard` writes `reports/dashboard.html`, a single file that works offline because the chart library is
embedded. It shows:
- the latest predictions with 80% and 95% intervals;
- the last 24 hours: CGM, the prediction made 30 minutes earlier with its intervals, boluses and basal;
- 14-day statistics: time in ranges, mean, variability, GMI, and glucose by time of day;
- test-period performance: error by horizon against persistence, interval coverage and the low-prediction result.

The running API serves the last generated file at `/dashboard`. It contains personal health data, so keep it local.

## Model registry and promotion

```
models/<model>/<h>m/<version>/{model.joblib, metadata.json, conformal.json}
models/registry.json      production version per horizon + promotion history (who/when/why/test MAE)
```

`metadata.json` records the features, hyperparameters, random seed, feature version, training period, software
versions, git commit, status and test metrics.

### Updating the model with new records

New records are used at three speeds. Every score below comes from data the scored model never trained on.

| How often | Command | What it does | Promotion |
|---|---|---|---|
| Daily | `make daily` | Scores yesterday, checks for drift, recalibrates the intervals | None (intervals only) |
| Monthly | `make refresh`, then `make refresh-compare` | Refits the same recipe on all data and runs it in shadow | Manual, on paired shadow days |
| Every 2–3 months | `make update-check`, then `make update` | Re-runs cross-validation on a new 8-week test period | Manual, on the test period or in shadow |

#### Daily: `make daily`

`make daily` syncs and ingests new records, then runs `python -m src.monitor`. It writes
`reports/monitor_report.md` and the DuckDB tables `monitor_predictions`, `monitor_daily` and
`interval_recalibrations`. Each production and shadow model is scored on every complete day after its training
data ended, against persistence on the same samples.

- **Drift:** a model is flagged when, over the last 7 days, its MAE divided by persistence MAE is more than 15%
  worse than on its earlier days, or when the 80% interval covers less than 70%.
- **Intervals:** each day is also scored with intervals recalibrated on the model's own errors from the previous
  56 days. When that rolling calibration has been at least as close to the nominal 80%/95% as the static one,
  `conformal_live.json` is written and the API uses it. Otherwise the static calibration stays.
- **API:** the running API is reloaded.

Settings are under `monitoring:` in `config/settings.yaml`. To run it unattended, add a cron entry such as
`15 6 * * * cd <repo> && make daily >> logs/daily.log 2>&1`.

#### Monthly: `make refresh`, then `make refresh-compare`

`python -m src.refresh fit` refits the production model on all data up to yesterday. The model type, target,
features and hyperparameters stay the same. The new version goes into **shadow**: `make daily` scores it next to
production on the same samples, and it is never served.

After `refresh.shadow_min_days` (14) paired days, `make refresh-compare` stores the paired result in the candidate's
metadata. When the candidate's MAE is not worse, it prints the promote command. Shadow evidence allows promotion
because both models are scored on identical data that neither trained on.

```sh
make refresh                # -> shadow version
make daily                  # every day, as usual
make refresh-compare        # after 14+ days
python -m src.registry promote --model random_forest --version <shadow> --reason "monthly refresh"
```

#### Every 2–3 months: re-selection with `make update`

New records can only be judged on data no model has seen, so each update uses a **rolling holdout**: the newest
weeks become the new fixed test period, the candidate is trained on everything before them, and the candidate and
the current production model are compared on those same weeks. Suggested rhythm: every 2–3 months.

```sh
make update-check           # sync + ingest, then: is there enough new data to roll? (prints the earliest date)
make update                 # full cycle; WEEKS=8 by default. Stops before promotion and prints the comparison
python -m src.registry promote --model random_forest --version <candidate> --reason "update cycle"
curl -X POST http://127.0.0.1:8000/admin/reload    # running API picks up the new production models
```

`make update` runs, stopping at the first failure:
1. **dbsync** (`PREDICT_DB=replica` only): copies new records from the source database to the replica.
2. **ingest:** loads them into DuckDB.
3. **Data-quality report:** read it for new pump-off gaps or sensor problems.
4. **Roll** (`python -m src.roll`): moves the window end to the latest complete day and makes the last 8 weeks the
   new test period. The old test period becomes training data. The roll is refused if the new test period would
   include data already used in any test evaluation, or if it has less than 70% CGM coverage. Each roll is recorded
   in `window_rolls`. The first possible roll is when data reaches 1 Dec 2026.
5. **Features**, with the leakage audit.
6. **Cross-validation** of random forest and persistence on the refreshed months. This re-checks the model and
   refreshes the interval calibration.
7. **Candidate:** a new version fitted on everything before the new test period.
8. **Scoring:** the candidate and the current production models on the new test period, recorded in
   `holdout_runs` and `experiments`. If production was trained on part of that period, as it will be after a
   monthly refresh, the candidate goes into shadow instead and is compared on live days.

Promotion is refused if the candidate is worse than production on the same data: the same test period, or the same
shadow days. It is also refused if production hasn't been scored on that data, or if the candidate has no interval
calibration. `--force` overrides this and requires a reason. Production's earlier test results are kept in its
metadata (`test_history`). In Docker, restart the container (`docker compose restart health-predict`) instead of calling `/admin/reload`, which only accepts
requests from the same machine.

## Experiment tracking and reproducibility

The DuckDB table `experiments` (`make experiments`) holds one row per cross-validation run and per test evaluation:
model, version, horizon, target and feature set, training, validation and test periods, features, hyperparameters,
MAE, RMSE, R², low-glucose recall and precision, 80% interval coverage, random seed, feature version, software
versions, git commit and time. New runs are logged automatically; `python -m src.tracking backfill` adds older ones.

Each run is reproducible from its git commit, the fixed seed (42) and the config files. The GPU path of the
sequence models is not bit-identical between runs.

## Limitations

- **One person, about 17 months of data with insulin.** Nothing here is expected to generalise to anyone else.
- **Lows:** point predictions pull toward the average, so a 3.9 cutoff on them misses most upcoming lows. Even with
  a validation-chosen cutoff, about three in four flags are false.
- **No exercise, illness, stress or sleep data.** Carbs come only from bolus entries.
- **Estimated IOB** is an approximation (exponential curve, 5 h duration), not physiological insulin activity.
- **Model differences are small:** nested tuning improved LightGBM by 1–2% and left the random forest and TCN
  unchanged, so the top models end up within about 1% of each other.
- **Censored readings:** sensor LOW/HIGH readings are stored at the limits, so their true value is beyond them.

## Project layout

```
config/          settings.yaml (thresholds, timezone, windows), mappings.yaml (source codes), models.yaml (models,
                 feature sets, selection rule, analysis)
src/             ingest, preprocessing, schema, validation, validate_data, plots, features, iob, targets, splits,
                 baselines, models, seq_models, sequences, train, evaluate, holdout, intervals, explain,
                 error_analysis, analyze, registry, tracking, serving, api, predict
tests/           tests (leakage audits, transformations, splits, models, intervals, registry, API)
notebooks/       01 data exploration · 02 features · 03 baselines · 04 model comparison · 05 error analysis
docker/          Dockerfile for the API (run by health-app's docker-compose.yml)
dbsync/          Go tool that mirrors a source Postgres to the replica (PREDICT_DB=replica)
data/, models/, reports/   generated (not committed)
```

`prompt.txt` is the original specification. Database credentials belong only in `.env`, which is never committed.
