"""Roll the modelling window forward so new records can be used.

    python -m src.roll --holdout-weeks 8 --dry-run     # show what would change
    python -m src.roll --holdout-weeks 8               # update config/settings.yaml

New layout after a roll:
    window end   = the latest complete local day with data
    test period  = the last `holdout-weeks` weeks of the window (the new fixed holdout)
    training     = everything before the new test period (including the old test period)

Rules (the roll is refused otherwise):
  * the new test period must start AFTER the current window end, so it contains only data that has never been
    used in any test-period evaluation;
  * if the production models were trained on part of the new test period (e.g. after a monthly refresh),
    production cannot be scored fairly there: the roll goes ahead, but the new candidate is then compared with
    production in shadow (python -m src.refresh) instead of on the test period;
  * CGM coverage in the new test period must be at least `--min-coverage` (default 70%).
Each roll is recorded in the DuckDB table `window_rolls`.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from src import config, db, registry


@dataclass
class RollPlan:
    old_end: date
    old_test_start: date
    new_end: date
    new_test_start: date
    production_overlap: bool = False


class RollError(ValueError):
    pass


def plan_roll(old_end: date, old_test_start: date, data_end: date, weeks: int,
              production_train_end: date | None) -> RollPlan:
    new_end = data_end
    new_test_start = new_end - timedelta(days=7 * weeks - 1)
    if new_test_start <= old_end:
        earliest = old_end + timedelta(days=7 * weeks)
        raise RollError(f"not enough new data: a {weeks}-week test period of never-evaluated data needs data through "
                        f"{earliest} (complete days); data currently ends {data_end}")
    overlap = production_train_end is not None and new_test_start <= production_train_end
    return RollPlan(old_end, old_test_start, new_end, new_test_start, overlap)


def apply_to_settings(path: Path, plan: RollPlan) -> None:
    """Rewrite the two dates in settings.yaml, keeping comments and layout."""
    text = path.read_text()
    for key, old, new in (("end", plan.old_end, plan.new_end), ("test_start", plan.old_test_start,
                                                                   plan.new_test_start)):
        pattern = rf'(\b{key}:\s*")({old.isoformat()})(")'
        text, n = re.subn(pattern, rf"\g<1>{new.isoformat()}\g<3>", text)
        if n != 1:
            raise RollError(f"expected exactly one '{key}: \"{old}\"' in {path}, found {n}")
    path.write_text(text)


def data_end_date(tz: str) -> date:
    """Latest complete local day with CGM data."""
    with db.duck(read_only=True) as d:
        last = d.execute("SELECT max(available_at) FROM cgm_merged").fetchone()[0]
    return (pd.Timestamp(last).tz_convert(tz) - pd.Timedelta(days=1)).date()


def coverage(start: date, end: date, settings: dict) -> float:
    tz, step = settings["project"]["timezone"], settings["cgm"]["expected_interval_minutes"]
    a = pd.Timestamp(start, tz=tz).tz_convert("UTC")
    b = (pd.Timestamp(end, tz=tz) + pd.Timedelta(days=1)).tz_convert("UTC")
    with db.duck(read_only=True) as d:
        n = d.execute(f"""SELECT count(DISTINCT time_bucket(INTERVAL '{step} minutes', measured_at)) FROM cgm_merged
                          WHERE measured_at >= ? AND measured_at < ?""", [a, b]).fetchone()[0]
    return n / ((b - a).total_seconds() / 60 / step)


def production_train_end(tz: str) -> date | None:
    ends = []
    for h, p in registry.load_registry()["production"].items():
        meta = registry.read_meta(p["model"], int(h), p["version"])
        ends.append(pd.Timestamp(meta["training_period"]["end"]).tz_convert(tz).date())
    return max(ends) if ends else None


def record(plan: RollPlan, cov: float, reason: str) -> None:
    from src.train import git_commit
    with db.duck() as d:
        d.execute("""CREATE TABLE IF NOT EXISTS window_rolls (rolled_at TIMESTAMPTZ, old_end DATE, old_test_start DATE,
                     new_end DATE, new_test_start DATE, test_coverage DOUBLE, reason VARCHAR, git_commit VARCHAR)""")
        d.execute("INSERT INTO window_rolls VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  [datetime.now(UTC), plan.old_end, plan.old_test_start, plan.new_end, plan.new_test_start, cov,
                   reason, git_commit()])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--holdout-weeks", type=int, default=8)
    ap.add_argument("--min-coverage", type=float, default=0.7)
    ap.add_argument("--reason", default="scheduled update")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    s = config.settings()
    tz = s["project"]["timezone"]
    old_end = date.fromisoformat(s["modelling_window"]["end"])
    old_test = date.fromisoformat(s["splits"]["test_start"])
    try:
        plan = plan_roll(old_end, old_test, data_end_date(tz), a.holdout_weeks, production_train_end(tz))
    except RollError as e:
        raise SystemExit(f"roll refused: {e}") from None
    cov = coverage(plan.new_test_start, plan.new_end, s)
    print(f"window end:  {plan.old_end} -> {plan.new_end}")
    print(f"test period: {plan.old_test_start}..{plan.old_end} -> {plan.new_test_start}..{plan.new_end} "
          f"(CGM coverage {cov:.0%})")
    print(f"training data now runs to {plan.new_test_start - timedelta(days=1)} (includes the old test period)")
    if plan.production_overlap:
        print("production was trained on part of the new test period: compare the new candidate in shadow "
              "(python -m src.refresh shadow ...), not on the test period")
    if cov < a.min_coverage:
        raise SystemExit(f"roll refused: CGM coverage in the new test period is {cov:.0%} (< {a.min_coverage:.0%})")
    if a.dry_run:
        print("dry run: nothing changed")
        return
    apply_to_settings(config.CONFIG_DIR / "settings.yaml", plan)
    record(plan, cov, a.reason)
    print("config/settings.yaml updated; next: make features, then the rest of `python -m src.update`")


if __name__ == "__main__":
    main()
