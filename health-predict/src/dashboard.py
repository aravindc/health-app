# ruff: noqa: C408  (dict(...) is the idiomatic way to write Plotly layout options)
"""Interactive dashboard (one self-contained HTML file, works offline).

    python -m src.dashboard              # -> reports/dashboard.html (also served by the API at /dashboard)
    python -m src.dashboard --at 2026-10-05T18:00:00Z

Shows the latest predictions, the last 24 hours (CGM, predictions made 30 minutes
earlier with their intervals, insulin), 14-day glucose statistics, and model
performance on the fixed test period. Contains personal health data: keep it local.

This system predicts glucose values for research and analysis. It is not a
medical device and must not be used to determine insulin doses or make treatment
decisions.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
from datetime import UTC, datetime

import pandas as pd
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs
from plotly.subplots import make_subplots

from src import DISCLAIMER, config, db, registry, serving
from src import features as F
from src import validation as v

log = logging.getLogger("dashboard")
GREEN, BLUE, PURPLE, ORANGE, RED, GREY = "#2f855a", "#2b6cb0", "#6b46c1", "#c05621", "#c53030", "#718096"


def _fig_html(fig: go.Figure) -> str:
    fig.update_layout(margin=dict(l=50, r=20, t=40, b=40), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="system-ui, sans-serif", size=12), legend=dict(orientation="h", y=-0.15))
    fig.update_xaxes(gridcolor="rgba(128,128,128,0.2)")
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.2)")
    return fig.to_html(full_html=False, include_plotlyjs=False, config={"displaylogo": False, "responsive": True})


def last_day(inputs, at: pd.Timestamp, thr: dict, tz: str, step_min: int = 15) -> tuple[str, list]:
    cgm, boluses, segments = inputs
    lo = at - pd.Timedelta(hours=24)
    times = pd.date_range(lo - pd.Timedelta(minutes=30), at - pd.Timedelta(minutes=30), freq=f"{step_min}min")
    rows = []
    for t in times:
        try:
            out = serving.predict(serving.history_from_db(t, inputs=inputs))
        except serving.InputError:
            continue
        p = out["predictions"]["30m"]
        rows.append({"target": t + pd.Timedelta(minutes=30), "value": p["value"], "lower": p["lower"],
                     "upper": p["upper"], "lower_95": p["lower_95"], "upper_95": p["upper_95"]})
    pr = pd.DataFrame(rows)
    c = cgm[(cgm["measured_at"] >= lo) & (cgm["available_at"] <= at)]
    b = boluses[(boluses["requested_at"] >= lo) & (boluses["requested_at"] <= at) & (boluses["units_delivered"] > 0)]
    sg = segments[(segments["start_at"] <= at) & (segments["end_at"].isna() | (segments["end_at"] >= lo))].copy()
    sg["start_at"] = sg["start_at"].clip(lower=lo)

    loc = lambda s: s.dt.tz_convert(tz) if hasattr(s, "dt") else s.tz_convert(tz)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.04)
    fig.add_hrect(y0=thr["low"], y1=thr["high"], fillcolor=GREEN, opacity=0.08, line_width=0, row=1, col=1)
    if len(pr):
        x = loc(pr["target"])
        fig.add_trace(go.Scatter(x=x, y=pr["upper_95"], line=dict(width=0), showlegend=False, hoverinfo="skip"), 1, 1)
        fig.add_trace(go.Scatter(x=x, y=pr["lower_95"], fill="tonexty", fillcolor="rgba(47,133,90,0.12)",
                                 line=dict(width=0), name="95% interval", hoverinfo="skip"), 1, 1)
        fig.add_trace(go.Scatter(x=x, y=pr["upper"], line=dict(width=0), showlegend=False, hoverinfo="skip"), 1, 1)
        fig.add_trace(go.Scatter(x=x, y=pr["lower"], fill="tonexty", fillcolor="rgba(47,133,90,0.25)",
                                 line=dict(width=0), name="80% interval", hoverinfo="skip"), 1, 1)
        fig.add_trace(go.Scatter(x=x, y=pr["value"], line=dict(color=GREEN, width=2), name="predicted 30 min earlier",
                                 hovertemplate="%{x|%H:%M} predicted %{y:.1f}<extra></extra>"), 1, 1)
    fig.add_trace(go.Scatter(x=loc(c["measured_at"]), y=c["glucose_mmol"], mode="markers",
                             marker=dict(color=BLUE, size=5), name="CGM",
                             hovertemplate="%{x|%H:%M} %{y:.1f} mmol/L<extra></extra>"), 1, 1)
    fig.add_trace(go.Bar(x=loc(b["requested_at"]), y=b["units_delivered"], marker_color=ORANGE, name="bolus (U)",
                         width=4 * 60_000, hovertemplate="%{x|%H:%M} %{y:.2f} U<extra></extra>"), 2, 1)
    if len(sg):
        fig.add_trace(go.Scatter(x=loc(sg["start_at"]), y=sg["rate_u_per_h"], line=dict(shape="hv", color=PURPLE),
                                 name="basal (U/h)"), 2, 1)
    fig.update_yaxes(title_text="mmol/L", row=1, col=1)
    fig.update_yaxes(title_text="insulin", row=2, col=1)
    fig.update_layout(height=520, title="Last 24 hours", hovermode="x unified")
    return _fig_html(fig), rows


def fourteen_days(cgm, at, settings, tz) -> tuple[str, str, dict]:
    thr = settings["glucose_thresholds_mmol"]
    c = cgm[(cgm["measured_at"] > at - pd.Timedelta(days=14)) & (cgm["available_at"] <= at)]
    m = v.cgm_metrics(c, settings, at - pd.Timedelta(days=14), at)
    labels = {"very_low": f"< {thr['very_low']}", "low": f"{thr['very_low']}–{thr['low']}",
              "target": f"{thr['low']}–{thr['high']}", "high": f"{thr['high']}–{thr['very_high']}",
              "very_high": f"> {thr['very_high']}"}
    colors = {"very_low": "#9b2c2c", "low": RED, "target": GREEN, "high": "#d69e2e", "very_high": "#975a16"}
    fig = go.Figure()
    for k in v.RANGE_ORDER:
        fig.add_trace(go.Bar(y=["14 days"], x=[m[f"pct_{k}"]], orientation="h", name=labels[k], marker_color=colors[k],
                             hovertemplate=f"{labels[k]}: %{{x:.1f}}%<extra></extra>"))
    fig.update_layout(barmode="stack", height=170, title="Time in ranges (mmol/L)", xaxis=dict(range=[0, 100],
                      ticksuffix="%"))
    local = c["measured_at"].dt.tz_convert(tz)
    tod = (local.dt.hour * 60 + local.dt.minute) // 15 * 15 / 60
    q = c.groupby(tod.to_numpy())["glucose_mmol"].quantile([0.05, 0.25, 0.5, 0.75, 0.95]).unstack()
    agp = go.Figure()
    agp.add_hrect(y0=thr["low"], y1=thr["high"], fillcolor=GREEN, opacity=0.08, line_width=0)
    for lo_, hi_, a in ((0.05, 0.95, 0.15), (0.25, 0.75, 0.35)):
        agp.add_trace(go.Scatter(x=q.index, y=q[hi_], line=dict(width=0), showlegend=False, hoverinfo="skip"))
        agp.add_trace(go.Scatter(x=q.index, y=q[lo_], fill="tonexty", fillcolor=f"rgba(43,108,176,{a})",
                                 line=dict(width=0), name=f"{int(lo_ * 100)}–{int(hi_ * 100)}th percentile"))
    agp.add_trace(go.Scatter(x=q.index, y=q[0.5], line=dict(color=BLUE, width=2), name="median"))
    agp.update_layout(height=320, title="Glucose by time of day (14 days)", xaxis=dict(title=f"hour ({tz})",
                      dtick=3, range=[0, 24]), yaxis=dict(title="mmol/L"))
    return _fig_html(fig), _fig_html(agp), m


def performance() -> tuple[str, list[list[str]]]:
    with db.duck(read_only=True) as d:
        ex = d.execute("""SELECT model, horizon, mae, prediction_interval_coverage FROM experiments
                          WHERE kind = 'test' AND model IN ('persistence', 'random_forest')
                          QUALIFY row_number() OVER (PARTITION BY model, horizon ORDER BY created_at DESC) = 1""").df()
        lows = d.execute("""SELECT mae_json FROM holdout_runs WHERE purpose = 'candidate-lows'
                            ORDER BY run_at DESC LIMIT 1""").fetchall()
    fig = go.Figure()
    for m, col, name in (("persistence", GREY, "persistence (no change)"), ("random_forest", GREEN, "selected model")):
        e = ex[ex["model"] == m].sort_values("horizon")
        fig.add_trace(go.Bar(x=[f"{h} min" for h in e["horizon"]], y=e["mae"], name=name, marker_color=col,
                             hovertemplate="%{x}: MAE %{y:.3f} mmol/L<extra></extra>"))
    fig.update_layout(height=300, title="Average error on the test period (lower is better)", barmode="group",
                      yaxis=dict(title="MAE (mmol/L)"))
    facts = []
    rf = ex[ex["model"] == "random_forest"].sort_values("horizon")
    if len(rf) and rf["prediction_interval_coverage"].notna().any():
        cov = ", ".join(f"{int(h)} min {c:.0%}" for h, c in zip(rf["horizon"], rf["prediction_interval_coverage"]))
        facts.append(["80% intervals contained the actual value", cov])
    if lows:
        j = json.loads(lows[0][0])
        k = next(iter(j))
        facts.append(["Upcoming lows flagged (within 30 min)",
                      f"{j[k]['sensitivity']:.0%} caught, {j[k]['precision']:.0%} of flags real ({k.split(' (')[0]})"])
    return _fig_html(fig), facts


def build(at: pd.Timestamp | None = None) -> str:
    s = config.settings()
    tz, thr = s["project"]["timezone"], s["glucose_thresholds_mmol"]
    with db.duck(read_only=True) as d:
        inputs = F.load_inputs(d)
    cgm = inputs[0]
    at = at or pd.Timestamp(cgm["available_at"].max()).tz_convert("UTC").floor("5min")
    now = serving.predict(serving.history_from_db(at, inputs=inputs))
    day_html, _ = last_day(inputs, at, thr, tz)
    tir_html, agp_html, m14 = fourteen_days(cgm, at, s, tz)
    perf_html, facts = performance()
    prod = registry.load_registry()["production"]

    cards = "".join(
        f'<div class="card"><div class="k">{h}</div><div class="v">{p["value"]:.1f}</div>'
        f'<div class="r">80%: {p["lower"]:.1f}–{p["upper"]:.1f}<br>95%: {p["lower_95"]:.1f}–{p["upper_95"]:.1f}</div></div>'
        for h, p in now["predictions"].items())
    warn = "".join(f"<li>{html.escape(w)}</li>" for w in now["warnings"])
    stats = [("Mean glucose", f"{m14['mean_mmol']:.1f} mmol/L"), ("Time in range", f"{m14['pct_target']:.0f}%"),
             ("Below range", f"{m14['pct_below_range']:.1f}%"), ("Variability (CV)", f"{m14['cv_pct']:.0f}%"),
             ("GMI", f"{m14['gmi_pct']:.1f}%"), ("CGM coverage", f"{m14['coverage_pct']:.0f}%")]
    stat_html = "".join(f'<div class="stat"><div class="k">{k}</div><div class="v2">{val}</div></div>' for k, val in stats)
    fact_html = "".join(f"<tr><td>{html.escape(a)}</td><td>{html.escape(b)}</td></tr>" for a, b in facts)
    versions = ", ".join(f"{h} min: {p['model']} {p['version']}" for h, p in sorted(prod.items(), key=lambda x: int(x[0])))
    local_at = at.tz_convert(tz)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Glucose Prediction Dashboard</title>
<style>
:root {{ --bg:#f7fafc; --panel:#ffffff; --text:#1a202c; --muted:#4a5568; --line:#e2e8f0; --warn:#fffaf0; --warnline:#dd6b20; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#171923; --panel:#1f2430; --text:#edf2f7;
  --muted:#a0aec0; --line:#2d3748; --warn:#2d2418; }} }}
:root[data-theme="dark"] {{ --bg:#171923; --panel:#1f2430; --text:#edf2f7; --muted:#a0aec0; --line:#2d3748; --warn:#2d2418; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui, -apple-system, Segoe UI, sans-serif; }}
main {{ max-width:1100px; margin:0 auto; padding:16px; }}
h1 {{ font-size:1.5rem; margin:8px 0 4px; }} h2 {{ font-size:1.1rem; margin:28px 0 8px; }}
.sub {{ color:var(--muted); margin:0 0 12px; }}
.disc {{ background:var(--warn); border-left:4px solid var(--warnline); padding:10px 14px; border-radius:6px; font-weight:600; }}
.panel {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px; margin:12px 0; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:10px; }}
.card, .stat {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px; }}
.k {{ color:var(--muted); font-size:.85rem; }} .v {{ font-size:2rem; font-weight:700; }} .v2 {{ font-size:1.3rem; font-weight:600; }}
.r {{ color:var(--muted); font-size:.85rem; }}
.stats {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); gap:10px; }}
table {{ width:100%; border-collapse:collapse; }} td {{ padding:6px 4px; border-bottom:1px solid var(--line); }}
ul.warn {{ color:var(--warnline); }}
footer {{ color:var(--muted); font-size:.8rem; margin:24px 0; }}
</style><script>{get_plotlyjs()}</script></head>
<body><main>
<h1>Glucose prediction — research dashboard</h1>
<p class="sub">Data up to {local_at:%a %d %b %Y, %H:%M} ({tz}) · current reading {now['current_glucose']:.1f} mmol/L</p>
<div class="disc">{html.escape(DISCLAIMER)} It makes no dosing, correction, basal or carbohydrate recommendations.</div>

<h2>Predicted glucose (mmol/L)</h2>
<div class="cards">{cards}</div>
{f'<ul class="warn">{warn}</ul>' if warn else ''}

<div class="panel">{day_html}</div>

<h2>Last 14 days</h2>
<div class="stats">{stat_html}</div>
<div class="panel">{tir_html}</div>
<div class="panel">{agp_html}</div>

<h2>How good are the predictions?</h2>
<div class="panel">{perf_html}<table>{fact_html}</table></div>

<footer>Models: {html.escape(versions)}. Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by
<code>python -m src.dashboard</code>. Contains personal health data — keep this file private.</footer>
</main></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--at", help="dashboard as of this time (ISO 8601 with timezone); default: latest data")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out = config.path(config.settings()["report"]["output_dir"]) / "dashboard.html"
    out.write_text(build(serving._utc(a.at) if a.at else None))
    log.info("wrote %s (%.1f MB)", out, out.stat().st_size / 1e6)


if __name__ == "__main__":
    main()
