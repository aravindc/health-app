"""Predict from the local database at a given time (what the system knew then).

    python -m src.predict                              # latest time with data
    python -m src.predict --at 2026-10-05T18:30:00Z
    python -m src.predict --at 2026-10-05T18:30:00Z --horizon 30

Uses the production models from the registry and the same code path as the API.
This system predicts glucose values for research and analysis. It is not a
medical device and must not be used to determine insulin doses or make treatment
decisions.
"""

from __future__ import annotations

import argparse
import json

import pandas as pd

from src import db, serving


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--at", help="prediction time (ISO 8601 with timezone); default: latest CGM reading")
    ap.add_argument("--horizon", type=int, help="show only this horizon (minutes)")
    a = ap.parse_args()
    if a.at:
        at = serving._utc(a.at)
    else:
        with db.duck(read_only=True) as d:
            at = pd.Timestamp(d.execute("SELECT max(available_at) FROM cgm_merged").fetchone()[0]).tz_convert("UTC")
    out = serving.predict(serving.history_from_db(at))
    if a.horizon:
        out["predictions"] = {k: v for k, v in out["predictions"].items() if k == f"{a.horizon}m"}
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
