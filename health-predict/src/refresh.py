"""Monthly refresh: refit the production recipe on all data, run it in shadow, promote on paired evidence.

    python -m src.refresh fit                  # refit on everything up to the last complete day -> shadow
    python -m src.refresh shadow --model random_forest --version <v>   # put any saved candidate in shadow
    python -m src.refresh compare              # paired comparison with production (after `make daily` has run)
    python -m src.refresh drop                 # stop shadowing
    (or: make refresh / make refresh-compare)

A refresh changes nothing about the model except the data: same model type, target, features and
hyperparameters as the production version it refreshes. The shadow model is scored every day by
`python -m src.monitor` on days after its own training end, on the same samples as production, and gets its
own interval calibration from those errors. `compare` stores the paired result in the candidate's metadata;
`python -m src.registry promote` then accepts it once there are `refresh.shadow_min_days` paired days and the
candidate's MAE is not worse. Nothing is promoted automatically.
"""

from __future__ import annotations

import argparse
import json
import logging
import platform
import shutil
from datetime import UTC, datetime

import joblib
import pandas as pd

from src import config, db, models, monitor, registry, roll, train
from src import features as F

log = logging.getLogger("refresh")


def fit(settings: dict, mcfg: dict, replace: bool = False) -> str:
    reg = registry.load_registry()
    if reg["shadow"] and not replace:
        raise SystemExit(f"a shadow model is already running ({reg['shadow']}); compare/promote it, or use --replace")
    if not reg["production"]:
        raise SystemExit("no production model to refresh")
    tz = settings["project"]["timezone"]
    data_end = roll.data_end_date(tz)
    start = F.window_bounds(settings)[0]
    emb = pd.Timedelta(minutes=settings["splits"]["embargo_minutes"])
    end = monitor.local_day_bounds(data_end, tz)[1]
    with db.duck(read_only=True) as d:
        inputs = F.load_inputs(d)
    df = monitor.dataset(inputs, settings, start, end)
    version = registry.version_id(datetime.now(UTC).isoformat(), train.git_commit())
    for h, p in sorted(reg["production"].items(), key=lambda kv: int(kv[0])):
        h = int(h)
        pm = registry.read_meta(p["model"], h, p["version"])
        if pm["model"] not in models.LEARNED or pm["model"] in models.SEQUENCE:
            raise SystemExit(f"refresh supports saved tabular models only, not {pm['model']}")
        missing = set(pm["features"]) - set(df.columns)
        if missing:
            raise SystemExit(f"production {h}m uses features this build does not produce: {sorted(missing)[:5]}")
        tr = train.usable(df, h, mcfg) & (df.index < end - emb)  # every target lies inside the data
        target = f"delta_{h}m" if pm["target_type"] == "delta" else f"glucose_{h}m"
        est = models.make_model(pm["model"], pm["hyperparameters"], pm["random_seed"])
        est.fit(df.loc[tr, pm["features"]], df.loc[tr, target])
        out = registry.version_dir(pm["model"], h, version)
        out.mkdir(parents=True, exist_ok=True)
        joblib.dump(est, out / "model.joblib")
        import sklearn
        meta = {k: pm[k] for k in ("model", "horizon", "target_type", "feature_set", "feature_version", "features",
                                   "hyperparameters", "random_seed")}
        meta |= {"version": version, "refresh_of": {"model": p["model"], "version": p["version"]},
                 "training_period": {"start": str(df.index[tr].min()), "end": str(df.index[tr].max()),
                                     "samples": int(tr.sum())},
                 "created_at": datetime.now(UTC).isoformat(), "git_commit": train.git_commit(),
                 "software": {"python": platform.python_version(), "scikit-learn": sklearn.__version__},
                 "status": "shadow — refreshed on newer data, not yet compared with production",
                 "conformal_source": f"copied from {p['version']} until its own shadow errors recalibrate it"}
        (out / "metadata.json").write_text(json.dumps(meta, indent=2))
        src_conf = registry.version_dir(p["model"], h, p["version"]) / registry.CONF
        if src_conf.exists():
            shutil.copy(src_conf, out / registry.CONF)
        reg["shadow"][str(h)] = {"model": pm["model"], "version": version, "since": datetime.now(UTC).isoformat()}
        log.info("%dm: fitted %s/%s on %d samples to %s", h, pm["model"], version, tr.sum(), df.index[tr].max())
    registry.save_registry(reg)
    return version


def set_shadow(model: str, version: str) -> None:
    reg = registry.load_registry()
    hs = [h for h in config.settings()["targets"]["horizons_minutes"] if version in registry.versions(model, h)]
    if not hs:
        raise SystemExit(f"no saved versions {model}/{version}")
    for h in hs:
        reg["shadow"][str(h)] = {"model": model, "version": version, "since": datetime.now(UTC).isoformat()}
    registry.save_registry(reg)


def paired(preds: pd.DataFrame, horizon: int, prod: dict, shadow: dict, min_day_samples: int) -> pd.DataFrame:
    """Per-day MAE of production and shadow on exactly the same samples."""
    key = ["ts", "horizon", "y_true", "day"]
    p = preds[(preds["horizon"] == horizon) & (preds["model"] == prod["model"]) & (preds["version"] == prod["version"])
              & (preds["role"] == "production")]
    s = preds[(preds["horizon"] == horizon) & (preds["model"] == shadow["model"])
              & (preds["version"] == shadow["version"]) & (preds["role"] == "shadow")]
    j = p[[*key, "y_pred"]].merge(s[["ts", "horizon", "y_pred"]], on=["ts", "horizon"], suffixes=("_prod", "_shadow"))
    j["ae_prod"], j["ae_shadow"] = (j["y_true"] - j["y_pred_prod"]).abs(), (j["y_true"] - j["y_pred_shadow"]).abs()
    d = j.groupby("day").agg(n=("ts", "size"), mae_prod=("ae_prod", "mean"), mae_shadow=("ae_shadow", "mean"))
    return d[d["n"] >= min_day_samples]


def compare(settings: dict) -> list[dict]:
    reg = registry.load_registry()
    if not reg["shadow"]:
        raise SystemExit("no shadow model; run `python -m src.refresh fit` first")
    with db.duck(read_only=True) as d:
        preds = d.execute(f"SELECT * FROM {monitor.PRED_TABLE}").df()
    preds["day"] = pd.to_datetime(preds["day"]).dt.date
    need = settings["refresh"]["shadow_min_days"]
    out = []
    for h, sh in sorted(reg["shadow"].items(), key=lambda kv: int(kv[0])):
        prod = reg["production"][h]
        h = int(h)
        d = paired(preds, h, prod, sh, settings["monitoring"]["min_day_samples"])
        if d.empty:
            out.append({"horizon": h, "days": 0})
            continue
        n = int(d["n"].sum())
        sc = {"production": {"model": prod["model"], "version": prod["version"]}, "days": len(d),
              "first_day": str(d.index.min()), "last_day": str(d.index.max()), "n": n,
              "mae": float((d["mae_shadow"] * d["n"]).sum() / n),
              "production_mae": float((d["mae_prod"] * d["n"]).sum() / n),
              "days_won": int((d["mae_shadow"] < d["mae_prod"]).sum()), "compared_at": datetime.now(UTC).isoformat()}
        meta = registry.read_meta(sh["model"], h, sh["version"])
        meta["shadow_comparison"] = sc
        meta["status"] = f"shadow — {sc['days']} paired days compared {datetime.now(UTC):%Y-%m-%d}"
        registry.write_meta(sh["model"], h, sh["version"], meta)
        out.append({"horizon": h, **sc, "model": sh["model"], "version": sh["version"],
                    "ready": sc["days"] >= need and sc["mae"] <= sc["production_mae"]})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fit")
    f.add_argument("--replace", action="store_true", help="replace the current shadow model")
    sp = sub.add_parser("shadow")
    sp.add_argument("--model", required=True)
    sp.add_argument("--version", required=True)
    sub.add_parser("compare")
    sub.add_parser("drop")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s, mcfg = config.settings(), config.models()
    if a.cmd == "fit":
        v = fit(s, mcfg, a.replace)
        print(f"shadow version {v}. `make daily` scores it from the first complete day after its training end; "
              f"compare after {s['refresh']['shadow_min_days']} days with `make refresh-compare`.")
    elif a.cmd == "shadow":
        set_shadow(a.model, a.version)
        print(f"{a.model}/{a.version} is now in shadow")
    elif a.cmd == "drop":
        reg = registry.load_registry()
        reg["shadow"] = {}
        registry.save_registry(reg)
        print("shadow cleared")
    else:
        need = s["refresh"]["shadow_min_days"]
        rows = compare(s)
        for r in rows:
            if not r["days"]:
                print(f"  {r['horizon']:>2}m: no paired days yet (run `make daily` after a complete day)")
                continue
            print(f"  {r['horizon']:>2}m: {r['days']} paired days ({r['first_day']}..{r['last_day']}): shadow MAE "
                  f"{r['mae']:.3f} vs production {r['production_mae']:.3f}, shadow better on {r['days_won']} days"
                  + ("  -> ready" if r["ready"] else f"  -> not ready (need {need} days and MAE not worse)"))
        ready = [r for r in rows if r.get("ready")]
        if ready:
            v = ready[0]["version"]
            hs = " ".join(str(r["horizon"]) for r in ready)
            print(f"\nTo promote: python -m src.registry promote --model {ready[0]['model']} --version {v} "
                  f"--horizon {hs} --reason \"monthly refresh\"\nthen: curl -X POST http://127.0.0.1:8000/admin/reload")


if __name__ == "__main__":
    main()
