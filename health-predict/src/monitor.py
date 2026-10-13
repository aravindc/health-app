"""Daily monitoring: score yesterday, track drift, recalibrate the prediction intervals.

    python -m src.monitor                  # score + recalibrate + report (reports/monitor_report.md)
    python -m src.monitor --sync           # first copy new records (dbsync) and ingest them (or: make daily)
    python -m src.monitor --no-recalibrate

Every production and shadow model is scored on each complete local day after its own training data ended (plus the
embargo), so every score is out-of-sample. Features come from `features.build_features`, the training code; the
leakage audit checks that this full build equals what was known at each prediction time.

Intervals. Two calibrations are scored side by side on every day:
  static   the model's conformal.json (out-of-fold validation errors from before its training end);
  rolling  refitted for each day on the model's own errors from the previous `recalibration_window_days` days
           (only days strictly before the scored day, so the check is honest).
`conformal_live.json` (used by the API) is written from the latest window when the rolling calibration has been
at least as close to the nominal coverage as the static one over the scored days; otherwise it is removed and the
static calibration is used. Each decision is recorded in the DuckDB table `interval_recalibrations`.

Drift. Over the last `drift_window_days` counted days, a production model is flagged when its skill (MAE divided by
persistence MAE on the same samples, so hard days are not mistaken for drift) is more than `drift_skill_tolerance`
worse than over its earlier scored days, or when the 80% interval covers less than `min_coverage_80`.

This system predicts glucose values for research and analysis. It is not a medical device.
"""

from __future__ import annotations

import argparse
import json
import logging
import urllib.request
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd

from src import config, db, registry, roll, targets, train
from src import features as F
from src import intervals as iv
from src.validate_data import md_table

log = logging.getLogger("monitor")
PRED_TABLE, DAY_TABLE, RECAL_TABLE = "monitor_predictions", "monitor_daily", "interval_recalibrations"


# ------------------------------------------------------------------ data

def dataset(inputs, settings: dict, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Features + targets for slot times in [start, end), from the clean tables (same code as training)."""
    cgm, boluses, segments = inputs
    feats, _ = F.build_features(cgm, boluses, segments, settings, start, end)
    tg = targets.make_targets(cgm, feats["current_glucose"], settings)
    return feats.join(tg.drop(columns=["current_glucose"]))


def local_day_bounds(day: date, tz: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    a = pd.Timestamp(day, tz=tz)
    return a.tz_convert("UTC"), (a + pd.Timedelta(days=1)).tz_convert("UTC")


def tracked_models() -> list[dict]:
    """Production and shadow (model, version, horizon) entries from the registry."""
    reg = registry.load_registry()
    out = []
    for role in ("production", "shadow"):
        for h, p in reg[role].items():
            out.append({"role": role, "model": p["model"], "version": p["version"], "horizon": int(h)})
    return out


def scored_from(meta: dict, settings: dict) -> pd.Timestamp:
    """First prediction time the model has never seen, even through a training target."""
    emb = pd.Timedelta(minutes=settings["splits"]["embargo_minutes"])
    step = pd.Timedelta(minutes=settings["cgm"]["expected_interval_minutes"])
    return pd.Timestamp(meta["training_period"]["end"]).tz_convert("UTC") + emb + step


def categories(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    c = config.models()["analysis"]["categories"]
    g = iv.glucose_range(df["current_glucose"], c["glucose_ranges_mmol"])
    d = iv.direction(df[c["rate_feature"]], c["stable_rate_mmol_per_min"], c["rapid_rate_mmol_per_min"])
    return g, d


def score_model(df: pd.DataFrame, entry: dict, settings: dict, mcfg: dict) -> pd.DataFrame:
    """Out-of-sample predictions of one model at one horizon, with its static intervals."""
    h = entry["horizon"]
    d = registry.version_dir(entry["model"], h, entry["version"])
    est, meta, _ = registry.load(entry["model"], h, entry["version"])
    static = (iv.conformal_from_dict(json.loads((d / registry.CONF).read_text()))
              if (d / registry.CONF).exists() else None)
    ok = train.usable(df, h, mcfg) & (df.index >= scored_from(meta, settings))
    x = df.loc[ok]
    if x.empty:
        return pd.DataFrame()
    pred = est.predict(x[meta["features"]])
    if meta["target_type"] == "delta":
        pred = pred + x["current_glucose"].to_numpy()
    g, dr = categories(x)
    out = pd.DataFrame({"ts": x.index, "horizon": h, "role": entry["role"], "model": entry["model"],
                        "version": entry["version"], "y_true": x[f"glucose_{h}m"].to_numpy(), "y_pred": pred,
                        "persistence": x["current_glucose"].to_numpy(), "grange": g.to_numpy(),
                        "direction": dr.to_numpy()})
    out["day"] = out["ts"].dt.tz_convert(settings["project"]["timezone"]).dt.date
    for L in mcfg["analysis"]["intervals"]["levels"]:
        k = round(L * 100)
        if static is None:
            out[f"lo{k}"] = out[f"hi{k}"] = np.nan
            continue
        lo, hi = static.interval(pred, out["grange"], out["direction"], L)
        out[f"lo{k}"], out[f"hi{k}"] = lo, hi
    return out


def counted_days(p: pd.DataFrame, settings: dict) -> list[date]:
    n = p.groupby("day").size()
    return sorted(n[n >= settings["monitoring"]["min_day_samples"]].index)


def fit_window(p: pd.DataFrame, levels: list[float], min_bin: int) -> iv.MondrianConformal:
    return iv.MondrianConformal(levels, min_bin).fit(pd.Series((p["y_true"] - p["y_pred"]).to_numpy()),
                                                    pd.Series(p["grange"].to_numpy()),
                                                    pd.Series(p["direction"].to_numpy()))


def add_rolling_intervals(p: pd.DataFrame, settings: dict, mcfg: dict) -> pd.DataFrame:
    """For each day, intervals calibrated on the same model's errors from the previous N days only."""
    mon, icfg = settings["monitoring"], mcfg["analysis"]["intervals"]
    levels, W = icfg["levels"], mon["recalibration_window_days"]
    days = set(counted_days(p, settings))
    p = p.copy()
    for L in levels:
        p[f"rlo{round(L * 100)}"] = p[f"rhi{round(L * 100)}"] = np.nan
    for day in sorted(p["day"].unique()):
        past_days = [d for d in days if day - timedelta(days=W) <= d < day]
        if len(past_days) < mon["recalibration_min_days"]:
            continue
        mc = fit_window(p[p["day"].isin(past_days)], levels, icfg["min_bin_size"])
        now = p["day"] == day
        for L in levels:
            k = round(L * 100)
            lo, hi = mc.interval(p.loc[now, "y_pred"].to_numpy(), p.loc[now, "grange"], p.loc[now, "direction"], L)
            p.loc[now, f"rlo{k}"], p.loc[now, f"rhi{k}"] = lo, hi
    return p


def daily_summary(p: pd.DataFrame, levels: list[float]) -> pd.DataFrame:
    rows = []
    for (role, model, version, h, day), g in p.groupby(["role", "model", "version", "horizon", "day"]):
        r = {"role": role, "model": model, "version": version, "horizon": h, "day": day, "n": len(g),
             "mae": float(np.mean(np.abs(g["y_true"] - g["y_pred"]))),
             "persistence_mae": float(np.mean(np.abs(g["y_true"] - g["persistence"])))}
        for L in levels:
            k = round(L * 100)
            for pre, name in (("", "static"), ("r", "rolling")):
                lo, hi = g[f"{pre}lo{k}"], g[f"{pre}hi{k}"]
                r[f"coverage{k}_{name}"] = (float(((g["y_true"] >= lo) & (g["y_true"] <= hi)).mean())
                                            if lo.notna().all() else np.nan)
        rows.append(r)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- checks

def coverage_error(p: pd.DataFrame, levels: list[float], prefix: str) -> float:
    """Mean |coverage − nominal| over levels, on rows where the calibration exists."""
    errs = []
    for L in levels:
        k = round(L * 100)
        m = p[f"{prefix}lo{k}"].notna()
        if not m.any():
            return np.nan
        inside = (p.loc[m, "y_true"] >= p.loc[m, f"{prefix}lo{k}"]) & (p.loc[m, "y_true"] <= p.loc[m, f"{prefix}hi{k}"])
        errs.append(abs(float(inside.mean()) - L))
    return float(np.mean(errs))


def drift_alerts(daily: pd.DataFrame, settings: dict) -> list[str]:
    mon = settings["monitoring"]
    out = []
    d = daily[(daily["role"] == "production") & (daily["n"] >= mon["min_day_samples"])]
    for (model, version, h), g in d.groupby(["model", "version", "horizon"]):
        g = g.sort_values("day")
        recent, before = g.tail(mon["drift_window_days"]), g.iloc[:-mon["drift_window_days"]]
        if recent.empty:
            continue
        skill = (recent["mae"] * recent["n"]).sum() / (recent["persistence_mae"] * recent["n"]).sum()
        if len(before) >= mon["recalibration_min_days"]:
            ref = (before["mae"] * before["n"]).sum() / (before["persistence_mae"] * before["n"]).sum()
            if skill > ref * (1 + mon["drift_skill_tolerance"]):
                out.append(f"{h}m {model}/{version}: last {len(recent)} days MAE/persistence {skill:.2f} vs {ref:.2f} "
                           f"before (> {mon['drift_skill_tolerance']:.0%} worse) — accuracy has drifted")
        col = "coverage80_rolling" if recent["coverage80_rolling"].notna().all() else "coverage80_static"
        cov = (recent[col] * recent["n"]).sum() / recent["n"].sum()
        if cov < mon["min_coverage_80"]:
            out.append(f"{h}m {model}/{version}: 80% interval covered {cov:.0%} over the last {len(recent)} days")
    return out


def recalibrate(p: pd.DataFrame, entry: dict, settings: dict, mcfg: dict) -> dict:
    """Write or remove conformal_live.json for one model/horizon; returns the recorded decision."""
    mon, icfg = settings["monitoring"], mcfg["analysis"]["intervals"]
    levels, cats = icfg["levels"], config.models()["analysis"]["categories"]
    days = counted_days(p, settings)
    window = days[-mon["recalibration_window_days"]:]
    rec = {"run_at": datetime.now(UTC), "model": entry["model"], "version": entry["version"],
           "horizon": entry["horizon"], "role": entry["role"], "days": len(window),
           "samples": int(p["day"].isin(window).sum()), "window_start": window[0] if window else None,
           "window_end": window[-1] if window else None, "applied": False}
    both = p[p[f"rlo{round(levels[0] * 100)}"].notna()]  # compare both calibrations on the same rows
    rec["static_error"], rec["rolling_error"] = coverage_error(both, levels, ""), coverage_error(both, levels, "r")
    live = registry.version_dir(entry["model"], entry["horizon"], entry["version"]) / registry.CONF_LIVE
    has_static = (live.parent / registry.CONF).exists()
    better = not np.isnan(rec["rolling_error"]) and (not has_static or np.isnan(rec["static_error"])
                                                     or rec["rolling_error"] <= rec["static_error"])
    if len(window) >= mon["recalibration_min_days"] and better:
        mc = fit_window(p[p["day"].isin(window)], levels, icfg["min_bin_size"])
        info = {k: cats[k] for k in ("glucose_ranges_mmol", "stable_rate_mmol_per_min", "rapid_rate_mmol_per_min",
                                     "rate_feature")}
        info |= {"calibration": "rolling: the model's own out-of-sample errors", "calibration_samples": rec["samples"],
                 "calibration_days": [str(window[0]), str(window[-1])], "created_at": rec["run_at"].isoformat()}
        live.write_text(json.dumps(iv.conformal_to_dict(mc, info), indent=2))
        rec["applied"] = True
    elif live.exists() and len(window) >= mon["recalibration_min_days"]:
        live.unlink()  # rolling was less accurate than static on the scored days: fall back to static
    return rec


# ----------------------------------------------------------------- store

def store(duck, name: str, df: pd.DataFrame, append: bool = False) -> None:
    duck.register("df", df)
    if append and duck.execute(f"SELECT count(*) FROM duckdb_tables() WHERE table_name = '{name}'").fetchone()[0]:
        duck.execute(f"INSERT INTO {name} BY NAME SELECT * FROM df")
    else:
        duck.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM df")
    duck.unregister("df")


def write_report(daily: pd.DataFrame, recs: list[dict], alerts: list[str], data_end: date, settings: dict) -> str:
    lines = ["# Daily monitoring", "", (f"Scored through {data_end} (last complete local day). Each model is scored "
                                         "only on days after its training data ended. Not a medical device."), ""]
    lines += ["## Alerts", ""] + ([f"- {a}" for a in alerts] or ["- none"]) + [""]
    show = daily[daily["day"] > data_end - timedelta(days=14)].copy()
    for role in ("production", "shadow"):
        r = show[show["role"] == role]
        if r.empty:
            continue
        lines += [f"## {role.capitalize()}: last 14 days", "", ("Each cell: MAE (persistence MAE) · 80% interval "
                  "coverage with the calibration in use that day (rolling when available, else static)."), ""]
        cov = r["coverage80_rolling"].fillna(r["coverage80_static"])
        r = r.assign(cell=[f"{m:.2f} ({pm:.2f}) · {c:.0%}" for m, pm, c in zip(r["mae"], r["persistence_mae"], cov)])
        tab = r.pivot_table(index="day", columns="horizon", values="cell", aggfunc="first")
        tab.columns = [f"{h} min" for h in tab.columns]
        lines += [md_table(tab.reset_index()), ""]
    lines += ["## Interval recalibration", "", ("Coverage error = mean |coverage − nominal| over 80% and 95%, on all "
                                                    "scored days where the rolling calibration existed."), ""]
    lines += [md_table(pd.DataFrame(recs)[["role", "horizon", "model", "version", "days", "samples", "static_error",
                                  "rolling_error", "applied"]], "{:.4f}"), ""]
    out = config.path(settings["report"]["output_dir"]) / "monitor_report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    return str(out)


def reload_api() -> str:
    try:
        req = urllib.request.Request("http://127.0.0.1:8000/admin/reload", method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return f"API reloaded ({r.status})"
    except OSError:
        return "API not running locally (nothing to reload)"


def run(recalibrate_intervals: bool = True) -> dict:
    s, mcfg = config.settings(), config.models()
    tz, levels = s["project"]["timezone"], mcfg["analysis"]["intervals"]["levels"]
    entries = tracked_models()
    if not entries:
        raise SystemExit("no production or shadow models in the registry")
    data_end = roll.data_end_date(tz)
    start = min(scored_from(registry.read_meta(e["model"], e["horizon"], e["version"]), s) for e in entries)
    end = local_day_bounds(data_end, tz)[1]
    with db.duck(read_only=True) as d:
        inputs = F.load_inputs(d)
    df = dataset(inputs, s, start.floor(f"{s['cgm']['expected_interval_minutes']}min"), end)
    preds, recs = [], []
    for e in entries:
        p = score_model(df, e, s, mcfg)
        if p.empty:
            log.info("%s %s/%s %dm: no complete day after its training end yet", e["role"], e["model"], e["version"],
                     e["horizon"])
            continue
        p = add_rolling_intervals(p, s, mcfg)
        if recalibrate_intervals:
            recs.append(recalibrate(p, e, s, mcfg))
        preds.append(p)
    if not preds:
        raise SystemExit("nothing to score yet")
    p = pd.concat(preds, ignore_index=True)
    daily = daily_summary(p, levels)
    alerts = drift_alerts(daily, s)
    with db.duck() as d:
        store(d, PRED_TABLE, p)
        store(d, DAY_TABLE, daily)
        if recs:
            store(d, RECAL_TABLE, pd.DataFrame(recs), append=True)
    report = write_report(daily, recs or [{k: None for k in ("role", "horizon", "model", "version", "days", "samples",
                                                             "static_error", "rolling_error", "applied")}],
                          alerts, data_end, s)
    return {"data_end": data_end, "daily": daily, "recalibrations": recs, "alerts": alerts, "report": report}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sync", action="store_true", help="copy new records (dbsync) and ingest them first")
    ap.add_argument("--no-recalibrate", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.sync:
        from src import update
        update.sync()
        update.run("ingest", update.PY + ["src.ingest"])
    r = run(not a.no_recalibrate)
    s = config.settings()
    d = r["daily"]
    last = d[(d["day"] == r["data_end"]) & (d["role"] == "production")].sort_values("horizon")
    print(f"\nscored through {r['data_end']}")
    for _, x in last.iterrows():
        print(f"  {x['horizon']:>2}m production MAE {x['mae']:.3f} (persistence {x['persistence_mae']:.3f}), "
              f"80% coverage {x['coverage80_static']:.0%} static / "
              + (f"{x['coverage80_rolling']:.0%} rolling" if not np.isnan(x["coverage80_rolling"]) else "– rolling"))
    for rec in r["recalibrations"]:
        print(f"  intervals {rec['role']} {rec['horizon']}m: " + (
            f"recalibrated on {rec['days']} days" if rec["applied"] else
            f"static kept ({rec['days']} days; rolling error {rec['rolling_error']:.3f} vs static "
            f"{rec['static_error']:.3f})" if not np.isnan(rec["rolling_error"]) else
            f"static kept (rolling needs {s['monitoring']['recalibration_min_days']} scored days)"))
    print("alerts:\n" + "\n".join(f"  ! {x}" for x in r["alerts"]) if r["alerts"] else "alerts: none")
    print(f"report: {r['report']}")
    if r["recalibrations"]:
        print(reload_api())


if __name__ == "__main__":
    main()
