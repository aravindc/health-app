"""Model registry.

Layout (spec section 22), one folder per trained version:

    models/<model>/<horizon>m/<version>/model.joblib
                                      /metadata.json   features, parameters, periods, metrics, git commit
                                      /conformal.json  interval calibration (from out-of-fold validation residuals)
                                      /conformal_live.json  recalibrated on the model's own recent errors
                                                       (python -m src.monitor); used instead when present
    models/registry.json                               production and shadow versions per horizon + history

Promotion rules (spec section 26): a newly trained model is only a *candidate*.
It can be promoted only with evidence that it is not worse than the current
production model on the same data (otherwise --force with a written reason):
  * test evidence: both scored on the current fixed test period (python -m src.holdout --candidate), or
  * shadow evidence: both scored on the same live days after the candidate's training end
    (python -m src.refresh compare), for at least refresh.shadow_min_days days.
Nothing is ever promoted automatically.

    python -m src.registry list
    python -m src.registry calibrate --model random_forest --version <v>      # (re)write conformal.json
    python -m src.registry promote --model random_forest --version <v> --reason "..."
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import joblib
import pandas as pd

from src import config
from src import intervals as iv

REGISTRY = "models/registry.json"
CONF, CONF_LIVE = "conformal.json", "conformal_live.json"


def model_root() -> Path:
    return config.path("models")


def version_id(created_at: str, git_commit: str | None) -> str:
    t = pd.Timestamp(created_at).tz_convert("UTC")
    return f"{t:%Y%m%d-%H%M}" + (f"-{git_commit[:7]}" if git_commit else "")


def version_dir(model: str, horizon: int, version: str) -> Path:
    return model_root() / model / f"{horizon}m" / version


def versions(model: str, horizon: int) -> list[str]:
    d = model_root() / model / f"{horizon}m"
    return sorted(p.name for p in d.iterdir() if (p / "metadata.json").exists()) if d.exists() else []


def read_meta(model: str, horizon: int, version: str) -> dict:
    return json.loads((version_dir(model, horizon, version) / "metadata.json").read_text())


def write_meta(model: str, horizon: int, version: str, meta: dict) -> None:
    (version_dir(model, horizon, version) / "metadata.json").write_text(json.dumps(meta, indent=2, default=str))


def load_registry() -> dict:
    p = config.path(REGISTRY)
    reg = json.loads(p.read_text()) if p.exists() else {"production": {}, "history": []}
    reg.setdefault("shadow", {})
    return reg


def save_registry(reg: dict) -> None:
    config.path(REGISTRY).write_text(json.dumps(reg, indent=2, default=str))


def production(horizon: int) -> dict | None:
    return load_registry()["production"].get(str(horizon))


def load(model: str, horizon: int, version: str):
    """(estimator, metadata, conformal or None) for one version. The live recalibration is preferred."""
    d = version_dir(model, horizon, version)
    conf = d / CONF_LIVE if (d / CONF_LIVE).exists() else d / CONF
    return (joblib.load(d / "model.joblib"), json.loads((d / "metadata.json").read_text()),
            iv.conformal_from_dict(json.loads(conf.read_text())) if conf.exists() else None)


def load_production(horizon: int):
    p = production(horizon)
    if p is None:
        raise LookupError(f"no production model for {horizon} min; promote one with `python -m src.registry promote`")
    return (*load(p["model"], horizon, p["version"]), p)


# ------------------------------------------------------------- calibration

def calibrate(model: str, horizon: int, version: str) -> Path:
    """Write conformal.json from out-of-fold validation residuals of the same model configuration."""
    from src import train

    s, mcfg = config.settings(), config.models()
    acfg = mcfg["analysis"]
    meta = read_meta(model, horizon, version)
    cv = pd.read_parquet(config.path(train.PRED_DIR) / "cv_predictions.parquet")
    cv = cv[(cv["model"] == model) & (cv["horizon"] == horizon) & (cv["target_type"] == meta["target_type"])
            & (cv["feature_set"] == meta["feature_set"])]
    if cv.empty:
        raise LookupError(f"no cross-validation predictions for {model}/{meta['target_type']}/{meta['feature_set']} "
                          f"at {horizon} min; run `python -m src.train --cv` for it first")
    df, _ = train.load_dataset(s)
    c = acfg["categories"]
    f = df.loc[pd.to_datetime(cv["ts"], utc=True).to_numpy()]
    g = iv.glucose_range(f["current_glucose"], c["glucose_ranges_mmol"])
    d = iv.direction(f[c["rate_feature"]], c["stable_rate_mmol_per_min"], c["rapid_rate_mmol_per_min"])
    res = pd.Series((cv["y_true"] - cv["y_pred"]).to_numpy())
    mc = iv.MondrianConformal(acfg["intervals"]["levels"], acfg["intervals"]["min_bin_size"]).fit(
        res, pd.Series(g.to_numpy()), pd.Series(d.to_numpy()))
    cats = {"glucose_ranges_mmol": c["glucose_ranges_mmol"], "stable_rate_mmol_per_min": c["stable_rate_mmol_per_min"],
            "rapid_rate_mmol_per_min": c["rapid_rate_mmol_per_min"], "rate_feature": c["rate_feature"],
            "calibration_samples": len(res), "calibration_folds": sorted(cv["fold"].unique().tolist())}
    out = version_dir(model, horizon, version) / "conformal.json"
    out.write_text(json.dumps(iv.conformal_to_dict(mc, cats), indent=2))
    return out


# --------------------------------------------------------------- promotion

def promote(model: str, horizon: int, version: str, reason: str, force: bool = False) -> dict:
    meta = read_meta(model, horizon, version)
    if meta.get("shadow_comparison") is not None:
        return promote_from_shadow(model, horizon, version, meta, reason, force)
    s = config.settings()
    if "test_metrics" not in meta:
        raise PermissionError(f"{model}/{horizon}m/{version} has not been evaluated on the fixed test period "
                              "(python -m src.holdout --candidate ...)")
    if (meta.get("test_period") or {}).get("start_local") != s["splits"]["test_start"]:
        raise PermissionError("candidate was not evaluated on the current fixed test period "
                              f"(starting {s['splits']['test_start']})")
    if not (version_dir(model, horizon, version) / "conformal.json").exists():
        raise PermissionError("candidate has no interval calibration (python -m src.registry calibrate ...)")
    reg = load_registry()
    current = reg["production"].get(str(horizon))
    if current and not force:
        cur_meta = read_meta(current["model"], horizon, current["version"])
        if (cur_meta.get("test_period") or {}).get("start_local") != s["splits"]["test_start"]:
            raise PermissionError(
                f"production ({current['model']}/{current['version']}) has not been scored on the current test period; "
                f"run `python -m src.holdout --candidate {current['model']} {current['version']}` first so both "
                "models are compared on the same data")
        cur_mae = cur_meta["test_metrics"]["mae"]
        if meta["test_metrics"]["mae"] > cur_mae:
            raise PermissionError(f"candidate test MAE {meta['test_metrics']['mae']:.3f} is worse than production "
                                  f"{cur_mae:.3f}; use --force with a reason to override")
    if force and not reason:
        raise PermissionError("--force needs --reason")
    return _record_promotion(reg, model, horizon, version, meta, reason, force, current, "test period",
                             meta["test_metrics"]["mae"])


def promote_from_shadow(model: str, horizon: int, version: str, meta: dict, reason: str, force: bool) -> dict:
    """Promotion on shadow evidence: candidate and production scored on the same live samples."""
    sc = meta["shadow_comparison"]
    reg = load_registry()
    current = reg["production"].get(str(horizon))
    if current and (sc["production"]["model"], sc["production"]["version"]) != (current["model"], current["version"]):
        raise PermissionError(f"the shadow comparison was made against {sc['production']['model']}/"
                              f"{sc['production']['version']}, but production is now {current['model']}/"
                              f"{current['version']}; run `python -m src.refresh compare` again")
    need = config.settings()["refresh"]["shadow_min_days"]
    if sc["days"] < need and not force:
        raise PermissionError(f"only {sc['days']} paired shadow days (need {need}); keep it in shadow longer")
    if not any((version_dir(model, horizon, version) / c).exists() for c in (CONF, CONF_LIVE)):
        raise PermissionError("candidate has no interval calibration")
    if not force and sc["mae"] > sc["production_mae"]:
        raise PermissionError(f"candidate shadow MAE {sc['mae']:.3f} is worse than production {sc['production_mae']:.3f} "
                              "on the same samples; use --force with a reason to override")
    if force and not reason:
        raise PermissionError("--force needs --reason")
    entry = _record_promotion(reg, model, horizon, version, meta, reason, force, current, "shadow", sc["mae"])
    reg = load_registry()
    if (reg["shadow"].get(str(horizon)) or {}).get("version") == version:
        del reg["shadow"][str(horizon)]
        save_registry(reg)
    return entry


def _record_promotion(reg, model, horizon, version, meta, reason, force, current, evidence, mae) -> dict:
    from src.train import git_commit
    entry = {"model": model, "version": version, "promoted_at": datetime.now(UTC).isoformat(),
             "test_mae": mae, "evidence": evidence, "reason": reason, "forced": force,
             "replaced": current, "git_commit": git_commit()}
    reg["production"][str(horizon)] = {k: entry[k] for k in ("model", "version", "promoted_at", "test_mae", "evidence")}
    reg["history"].append({"horizon": horizon, **entry})
    save_registry(reg)
    meta["status"] = "production"
    write_meta(model, horizon, version, meta)
    if current and (current["model"], current["version"]) != (model, version):
        old = read_meta(current["model"], horizon, current["version"])
        old["status"] = f"retired {datetime.now(UTC):%Y-%m-%d}"
        write_meta(current["model"], horizon, current["version"], old)
    return entry


def migrate_flat_layout() -> list[str]:
    """Move models saved as models/<m>/<h>m/model.joblib into a version folder (one-off)."""
    moved = []
    for meta_path in model_root().glob("*/*m/metadata.json"):
        meta = json.loads(meta_path.read_text())
        v = version_id(meta["created_at"], meta.get("git_commit"))
        dest = meta_path.parent / v
        dest.mkdir(exist_ok=True)
        for f in ("model.joblib", "metadata.json", "conformal.json"):
            if (meta_path.parent / f).exists():
                shutil.move(str(meta_path.parent / f), dest / f)
        meta["version"] = v
        if "test_metrics" in meta and "test_period" not in meta:
            meta["test_period"] = {"start_local": config.settings()["splits"]["test_start"]}
        (dest / "metadata.json").write_text(json.dumps(meta, indent=2))
        moved.append(str(dest.relative_to(model_root())))
    return moved


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("migrate")
    for name in ("calibrate", "promote"):
        p = sub.add_parser(name)
        p.add_argument("--model", required=True)
        p.add_argument("--version", required=True, help='version id, or "latest"')
        p.add_argument("--horizon", type=int, nargs="*")
        if name == "promote":
            p.add_argument("--reason", required=True)
            p.add_argument("--force", action="store_true")
    a = ap.parse_args()
    horizons = getattr(a, "horizon", None) or config.settings()["targets"]["horizons_minutes"]

    def resolve(h):  # "latest" = newest saved version for that horizon
        return versions(a.model, h)[-1] if a.version == "latest" else a.version
    if a.cmd == "migrate":
        print("\n".join(migrate_flat_layout()) or "nothing to migrate")
    elif a.cmd == "calibrate":
        for h in horizons:
            print(calibrate(a.model, h, resolve(h)))
    elif a.cmd == "promote":
        for h in horizons:
            e = promote(a.model, h, resolve(h), a.reason, a.force)
            print(f"{h}m: {e['model']}/{e['version']} is now production ({e['evidence']} MAE {e['test_mae']:.3f})")
    else:
        reg = load_registry()
        rows = []
        for mdir in sorted(model_root().glob("*/*m/*/metadata.json")):
            m = json.loads(mdir.read_text())
            prod = reg["production"].get(str(m["horizon"]), {})
            rows.append({"model": m["model"], "horizon": m["horizon"], "version": m.get("version", mdir.parent.name),
                         "status": m.get("status"), "test MAE": (m.get("test_metrics") or {}).get("mae"),
                         "production": prod.get("version") == m.get("version") and prod.get("model") == m["model"],
                         "shadow": (reg["shadow"].get(str(m["horizon"])) or {}).get("version") == m.get("version")})
        print(pd.DataFrame(rows).to_string(index=False) if rows else "no models")


if __name__ == "__main__":
    main()
