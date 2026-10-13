"""Update cycle: new records -> new candidate model, compared with production on fresh data.

    python -m src.update                      # full cycle (or: make update)
    python -m src.update --holdout-weeks 8 --skip-sync
    python -m src.update --check              # only report whether enough new data exists to roll

Steps (each a separate command, stops at the first failure):
  1. dbsync            with PREDICT_DB=replica: copy new records from the source Postgres to the replica
  2. ingest            load them into DuckDB, rebuild clean tables
  3. validate          data-quality report (look at it: new pump-off gaps, sensor problems)
  4. roll              move the window end forward; the newest weeks become the new fixed test period
  5. features          rebuild features, targets and splits (with the leakage audit)
  6. cross-validation  random forest + persistence on the refreshed folds (re-check, interval calibration)
  7. final             fit a new CANDIDATE version on everything before the new test period
  8. score            the candidate AND the current production models on the new test period
                     (if production was trained on part of it, e.g. after a monthly refresh, the candidate goes
                     into shadow instead and is compared with production on live days: python -m src.refresh)
It never promotes. Promotion stays a manual, recorded decision (the command is printed at the end).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

from src import config, db, registry, roll

PY = [sys.executable, "-m"]


def run(step: str, cmd: list[str], cwd=None, informational: bool = False) -> bool:
    print(f"\n=== {step}: {' '.join(cmd)}", flush=True)
    t0 = time.time()
    r = subprocess.run(cmd, cwd=cwd or config.ROOT, check=False)  # return code handled below
    if r.returncode != 0 and informational:
        return False
    if r.returncode != 0:
        raise SystemExit(f"update stopped: step '{step}' failed (exit {r.returncode}); nothing after it was run")
    print(f"=== {step}: done in {time.time() - t0:.0f}s", flush=True)
    return True


def sync() -> None:
    """Copy new records into the replica (PREDICT_DB=replica). health-db needs no copy: ingest reads it."""
    if db.source() != db.REPLICA:
        print("\n=== sync: skipped (PREDICT_DB=health-db is read directly)", flush=True)
        return
    d = config.ROOT / "dbsync"
    if not (d / "dbsync").exists():
        run("build dbsync", ["go", "build", "-o", "dbsync", "."], cwd=d)
    run("sync source -> local replica", ["./dbsync"], cwd=d)


def latest_version(model: str) -> str:
    vs = {v for h in config.settings()["targets"]["horizons_minutes"] for v in registry.versions(model, h)}
    return max(vs)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--holdout-weeks", type=int, default=8)
    ap.add_argument("--skip-sync", action="store_true", help="do not run dbsync (the replica is current)")
    ap.add_argument("--check", action="store_true", help="sync + ingest, then only check whether a roll is possible")
    a = ap.parse_args()

    if not a.skip_sync:
        sync()
    run("ingest", PY + ["src.ingest"])
    if a.check:
        ok = run("roll check", PY + ["src.roll", "--holdout-weeks", str(a.holdout_weeks), "--dry-run"],
                 informational=True)
        print("\n=== " + ("ready: run `make update`" if ok else "not ready yet: keep collecting data (see above)"))
        return
    run("data-quality report", PY + ["src.validate_data"])
    run("roll window", PY + ["src.roll", "--holdout-weeks", str(a.holdout_weeks)])
    run("features + leakage audit", PY + ["src.features", "--audit", "40"])
    run("cross-validation (random forest, persistence)",
        PY + ["src.train", "--cv", "--model", "persistence", "random_forest", "--target-type", "delta",
              "--feature-set", "glucose_insulin"])
    run("fit candidate", PY + ["src.train", "--final", "--model", "random_forest", "--target-type", "delta",
                               "--feature-set", "glucose_insulin"])
    cand = latest_version("random_forest")
    run("score candidate on the new test period", PY + ["src.holdout", "--candidate", "random_forest", cand])
    prod = registry.load_registry()["production"]
    s = config.settings()
    test_start = roll.date.fromisoformat(s["splits"]["test_start"])
    overlap = (roll.production_train_end(s["project"]["timezone"]) or test_start) >= test_start
    if overlap:
        run("put candidate in shadow", PY + ["src.refresh", "shadow", "--model", "random_forest", "--version", cand])
        print(f"\nProduction was trained on part of the new test period, so it cannot be scored there fairly. "
              f"Candidate {cand} is now in shadow: `make daily` scores both on the same live days; after "
              f"{s['refresh']['shadow_min_days']} days run `make refresh-compare`. Nothing has been promoted.")
        return
    for model, version in sorted({(p["model"], p["version"]) for p in prod.values()}):
        if (model, version) != ("random_forest", cand):
            run(f"re-score production {model}/{version}", PY + ["src.holdout", "--candidate", model, version])

    print("\n=== comparison on the new test period (MAE, mmol/L)")
    for h in config.settings()["targets"]["horizons_minutes"]:
        c = registry.read_meta("random_forest", h, cand)["test_metrics"]["mae"]
        p = prod.get(str(h))
        pm = registry.read_meta(p["model"], h, p["version"])["test_metrics"]["mae"] if p else None
        verdict = "candidate better" if pm is None or c < pm else "production better or equal"
        print(f"  {h:>2} min: candidate {c:.3f}" + (f" | production {pm:.3f}" if pm is not None else "")
              + f"  -> {verdict}")
    print(f"\nCandidate version: {cand}. Nothing has been promoted. To promote (refused if worse):\n"
          f"  python -m src.registry promote --model random_forest --version {cand} --reason \"update cycle\"\n"
          "then reload the running API: curl -X POST http://127.0.0.1:8000/admin/reload")
    print(json.dumps({"candidate": cand}))


if __name__ == "__main__":
    main()
