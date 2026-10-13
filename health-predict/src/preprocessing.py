"""Cleaning transformations from raw source rows to the clean tables.

Every function is pure (DataFrame in, DataFrame out) so it can be tested in
isolation. Problems are never dropped silently: each function returns an
issues frame alongside its output, and excluded rows stay in the output with
an `exclude_reason`.

Assumptions are documented inline and in reports/data_quality_report.md.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

ISSUE_COLUMNS = ["source_table", "source_pk", "issue_type", "severity", "occurred_at", "detail"]


def issues_frame(source_table: str, rows: pd.DataFrame, issue_type: str, severity: str,
                 pk_col: str, time_col: str, detail: pd.Series | str | None = None) -> pd.DataFrame:
    out = pd.DataFrame({
        "source_table": source_table,
        "source_pk": rows[pk_col].astype("int64").to_numpy(),
        "issue_type": issue_type,
        "severity": severity,
        "occurred_at": rows[time_col].to_numpy(),
        "detail": detail.to_numpy() if isinstance(detail, pd.Series) else detail,
    })
    return out


def concat_issues(frames: list[pd.DataFrame]) -> pd.DataFrame:
    frames = [f for f in frames if len(f)]
    if not frames:
        return pd.DataFrame(columns=ISSUE_COLUMNS)
    return pd.concat(frames, ignore_index=True)[ISSUE_COLUMNS]


def _utc(s: pd.Series) -> pd.Series:
    """UTC timestamps at a single resolution (sources mix ms and us)."""
    return pd.to_datetime(s, utc=True).dt.as_unit("ns")


def _trend(codes: pd.Series, mapping: dict[int, dict[str, Any]]) -> tuple[pd.Series, pd.Series]:
    names = codes.map(lambda c: mapping.get(int(c), {}).get("name") if pd.notna(c) else None)
    ords = codes.map(lambda c: mapping.get(int(c), {}).get("ordinal") if pd.notna(c) else None)
    return names, pd.to_numeric(ords, errors="coerce").astype("Int16")


def _censor(sgv: pd.Series, low_mask: pd.Series, high_mask: pd.Series, cgm_cfg: dict) -> tuple[pd.Series, pd.Series]:
    mgdl = sgv.astype(float).copy()
    mgdl[low_mask] = cgm_cfg["sensor_low_limit_mgdl"]
    mgdl[high_mask] = cgm_cfg["sensor_high_limit_mgdl"]
    censored = pd.Series(np.where(low_mask, "low", np.where(high_mask, "high", None)), index=sgv.index,
                         dtype="object")
    return mgdl, censored


def _flag_values(df: pd.DataFrame, cgm_cfg: dict) -> pd.Series:
    """Impossible values (not explained by a LOW/HIGH flag) are excluded."""
    bad = df["censored"].isna() & (
        (df["glucose_mmol"] < cgm_cfg["impossible_min_mmol"]) | (df["glucose_mmol"] > cgm_cfg["impossible_max_mmol"])
        | df["glucose_mmol"].isna()
    )
    return bad


def flag_suspicious_rates(df: pd.DataFrame, max_rate: float, min_interval_minutes: float = 3) -> pd.Series:
    """Readings whose change from the previous usable reading exceeds max_rate (mmol/L/min).

    Only checked when readings are at least `min_interval_minutes` apart: over
    shorter spacing, small sensor noise turns into a large apparent rate.
    Suspicious readings are flagged, not excluded.
    """
    usable = df[df["exclude_reason"].isna()].sort_values("measured_at")
    dt_min = usable["measured_at"].diff().dt.total_seconds() / 60
    rate = usable["glucose_mmol"].diff().abs() / dt_min
    sus = (dt_min >= min_interval_minutes) & (rate > max_rate)
    out = pd.Series(False, index=df.index)
    out.loc[sus[sus].index] = True
    return out


# ---------------------------------------------------------------- CGM: Tandem

def clean_tandem_cgm(raw: pd.DataFrame, settings: dict, mappings: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Clean tandem_cgm rows.

    Backfilled readings (cgmDataType contains 1) are logged by the pump at
    arrival time (`reading_at`), not at measurement time. Their measurement
    time is reconstructed from the sensor timestamp `egvTimeStamp` (pump clock,
    seconds) plus the pump-clock offset of the nearest live reading:

        measured_at = egv + (live.reading_at - live.egv)

    This is validated against Nightscout: exact value matches rise from 6% to
    63% after correction. `available_at` keeps the arrival time.
    """
    cgm_cfg = settings["cgm"]
    factor = settings["units"]["mgdl_per_mmol"]
    df = raw.copy()
    df["reading_at"] = _utc(df["reading_at"])
    props = df["event_properties"].map(lambda s: json.loads(s) if isinstance(s, str) else (s or {}))
    df["egv"] = props.map(lambda p: p.get("egvTimeStamp")).astype("Int64")
    df["is_backfill"] = props.map(lambda p: 1 in (p.get("cgmDataType") or []))

    # Pump-clock origin per live reading: reading_at - egv seconds.
    live = df[~df["is_backfill"] & df["egv"].notna()][["egv", "reading_at"]].copy()
    live["origin"] = live["reading_at"] - pd.to_timedelta(live["egv"].astype("int64"), unit="s")
    live = live.sort_values("egv")[["egv", "origin"]]
    live["egv"] = live["egv"].astype("int64")

    df["measured_at"] = df["reading_at"]
    df["time_corrected"] = False
    bf = df[df["is_backfill"] & df["egv"].notna()].copy()
    if len(bf) and len(live):
        bf["egv"] = bf["egv"].astype("int64")
        m = pd.merge_asof(bf.reset_index().sort_values("egv"), live, on="egv", direction="nearest",
                          tolerance=cgm_cfg["backfill_offset_tolerance_seconds"]).set_index("index")
        ok = m["origin"].notna()
        corrected = m.loc[ok, "origin"] + pd.to_timedelta(m.loc[ok, "egv"], unit="s")
        df.loc[corrected.index, "measured_at"] = corrected.dt.as_unit("ns")
        df.loc[corrected.index, "time_corrected"] = True
    uncorrected = df[df["is_backfill"] & ~df["time_corrected"]]
    df["available_at"] = df[["reading_at", "measured_at"]].max(axis=1)

    status = df["glucose_value_status"]
    low, high = status.eq(2), status.eq(1)
    df["glucose_mgdl"], df["censored"] = _censor(df["sgv"], low, high, cgm_cfg)
    df["glucose_mmol"] = df["glucose_mgdl"] / factor
    df["trend_name"], df["trend_ordinal"] = _trend(df["trend"], mappings["cgm_trend"]["tandem"])
    df["rate_mmol_per_min"] = df["rate"] * mappings["tandem_rate_mgdl_per_min_per_unit"] / factor

    # Duplicates share egvTimeStamp. Prefer live over backfill, then the latest sequence number.
    df = df.sort_values(["egv", "is_backfill", "sequence_number"], ascending=[True, True, False])
    dup = df["egv"].notna() & df.duplicated("egv", keep="first")
    first_val = df.groupby("egv")["sgv"].transform("first")
    df["exclude_reason"] = None
    df.loc[dup, "exclude_reason"] = "duplicate"
    impossible = _flag_values(df, cgm_cfg) & df["exclude_reason"].isna()
    df.loc[impossible, "exclude_reason"] = "impossible_value"
    df["is_suspicious"] = flag_suspicious_rates(df, cgm_cfg["suspicious_rate_mmol_per_min"],
                                               cgm_cfg["rate_check_min_interval_minutes"])

    low, high = df["censored"].eq("low"), df["censored"].eq("high")
    t = "tandem_cgm"
    issues = concat_issues([
        issues_frame(t, df[dup], "duplicate_reading", "excluded", "id", "measured_at",
                     np.where(df.loc[dup, "sgv"] != first_val[dup], "value conflicts with kept reading",
                              "identical to kept reading")),
        issues_frame(t, df[df["time_corrected"]], "backfill_time_corrected", "info", "id", "measured_at",
                     "measured_at shifted by " + ((df.loc[df["time_corrected"], "reading_at"]
                                                   - df.loc[df["time_corrected"], "measured_at"])
                                                  .dt.total_seconds().round().astype(int).astype(str)) + "s"),
        issues_frame(t, uncorrected, "backfill_time_uncorrected", "warning", "id", "measured_at",
                     "no live reading near this backfill; measured_at = arrival time"),
        issues_frame(t, df[low], "censored_low", "info", "id", "measured_at",
                     "sensor LOW; raw sgv " + df.loc[low, "sgv"].astype(str)),
        issues_frame(t, df[high], "censored_high", "info", "id", "measured_at",
                     "sensor HIGH; raw sgv " + df.loc[high, "sgv"].astype(str)),
        issues_frame(t, df[impossible], "impossible_value", "excluded", "id", "measured_at",
                     "sgv " + df.loc[impossible, "sgv"].astype(str)),
        issues_frame(t, df[df["is_suspicious"]], "suspicious_rate_of_change", "warning", "id", "measured_at"),
    ])

    out = pd.DataFrame({
        "reading_id": "tandem:" + df["id"].astype(str),
        "source": "tandem",
        "source_pk": df["id"].astype("int64"),
        "measured_at": df["measured_at"],
        "available_at": df["available_at"],
        "glucose_mmol": df["glucose_mmol"],
        "glucose_mgdl": df["glucose_mgdl"],
        "sgv_raw": df["sgv"].astype(float),
        "censored": df["censored"],
        "trend_name": df["trend_name"],
        "trend_ordinal": df["trend_ordinal"],
        "rate_mmol_per_min": df["rate_mmol_per_min"],
        "is_backfill": df["is_backfill"].astype(bool),
        "time_corrected": df["time_corrected"].astype(bool),
        "is_suspicious": df["is_suspicious"].astype(bool),
        "exclude_reason": df["exclude_reason"],
    }).sort_values("measured_at").reset_index(drop=True)
    return out, issues


# ------------------------------------------------------------ CGM: Nightscout

def clean_nightscout_cgm(raw: pd.DataFrame, settings: dict, mappings: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Clean nightscoutdb rows. Nightscout encodes sensor LOW as 39 and HIGH as 401.

    Assumption: Nightscout values were uploaded live, so available_at = measured_at.
    """
    cgm_cfg = settings["cgm"]
    factor = settings["units"]["mgdl_per_mmol"]
    df = raw.copy()
    df["measured_at"] = _utc(pd.to_datetime(df["ns_time"], unit="ms", utc=True))
    low, high = df["sgv"].le(39), df["sgv"].ge(401)
    df["glucose_mgdl"], df["censored"] = _censor(df["sgv"], low, high, cgm_cfg)
    df["glucose_mmol"] = df["glucose_mgdl"] / factor
    df["trend_name"], df["trend_ordinal"] = _trend(df["trend"], mappings["cgm_trend"]["nightscout"])

    df = df.sort_values(["ns_time", "id"])
    dup = df.duplicated("ns_time", keep="last")
    df["exclude_reason"] = None
    df.loc[dup, "exclude_reason"] = "duplicate"
    impossible = _flag_values(df, cgm_cfg) & df["exclude_reason"].isna()
    df.loc[impossible, "exclude_reason"] = "impossible_value"
    df["is_suspicious"] = flag_suspicious_rates(df, cgm_cfg["suspicious_rate_mmol_per_min"],
                                               cgm_cfg["rate_check_min_interval_minutes"])

    low, high = df["censored"].eq("low"), df["censored"].eq("high")
    t = "nightscoutdb"
    issues = concat_issues([
        issues_frame(t, df[dup], "duplicate_reading", "excluded", "id", "measured_at"),
        issues_frame(t, df[low], "censored_low", "info", "id", "measured_at", "sensor LOW (sgv 39)"),
        issues_frame(t, df[high], "censored_high", "info", "id", "measured_at", "sensor HIGH (sgv 401)"),
        issues_frame(t, df[impossible], "impossible_value", "excluded", "id", "measured_at",
                     "sgv " + df.loc[impossible, "sgv"].astype(str)),
        issues_frame(t, df[df["is_suspicious"]], "suspicious_rate_of_change", "warning", "id", "measured_at"),
    ])

    out = pd.DataFrame({
        "reading_id": "nightscout:" + df["id"].astype(str),
        "source": "nightscout",
        "source_pk": df["id"].astype("int64"),
        "measured_at": df["measured_at"],
        "available_at": df["measured_at"],
        "glucose_mmol": df["glucose_mmol"],
        "glucose_mgdl": df["glucose_mgdl"],
        "sgv_raw": df["sgv"].astype(float),
        "censored": df["censored"],
        "trend_name": df["trend_name"],
        "trend_ordinal": df["trend_ordinal"],
        "rate_mmol_per_min": np.nan,
        "is_backfill": False,
        "time_corrected": False,
        "is_suspicious": df["is_suspicious"].astype(bool),
        "exclude_reason": df["exclude_reason"],
    }).sort_values("measured_at").reset_index(drop=True)
    return out, issues


def merge_cgm(tandem: pd.DataFrame, nightscout: pd.DataFrame, tolerance_seconds: int) -> pd.DataFrame:
    """Primary series: usable Tandem readings, plus usable Nightscout readings
    that have no usable Tandem reading within `tolerance_seconds`.
    """
    cols = ["measured_at", "available_at", "glucose_mmol", "censored", "trend_ordinal", "rate_mmol_per_min",
            "source", "reading_id"]
    t = tandem[tandem["exclude_reason"].isna()].sort_values("measured_at")
    n = nightscout[nightscout["exclude_reason"].isna()].sort_values("measured_at")
    if len(t) == 0:
        return n[cols].reset_index(drop=True)
    probe = pd.merge_asof(n[["measured_at"]].reset_index(),
                          t[["measured_at"]].assign(t_at=t["measured_at"]),
                          on="measured_at", direction="nearest",
                          tolerance=pd.Timedelta(seconds=tolerance_seconds)).set_index("index")
    fill = n.loc[probe.index[probe["t_at"].isna()]]
    return pd.concat([t[cols], fill[cols]]).sort_values("measured_at").reset_index(drop=True)


# ------------------------------------------------------------------ Boluses

def match_bolus_rule(rules: list[dict], bolus_type: int | None, options: int | None, has_carbs: bool) -> dict:
    facts = {"bolus_type": bolus_type, "options": options, "has_carbs": has_carbs}
    for rule in rules:
        if all(facts.get(k) == v for k, v in rule["match"].items()):
            return rule
    raise ValueError("bolus_rules needs a catch-all rule with match: {}")


def clean_boluses(raw: pd.DataFrame, settings: dict, mappings: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Map tandem_bolus rows to delivery types (config/mappings.yaml: bolus_rules).

    Units delivered = `insulin_delivered` (cancelled boluses may deliver a part).
    Extended boluses: `standardPercent` of the requested dose is delivered at
    once and the rest is spread evenly until `completed_at`.
    """
    ins_cfg = settings["insulin"]
    factor = settings["units"]["mgdl_per_mmol"]
    df = raw.copy()
    df["requested_at"] = _utc(df["requested_at"])
    df["completed_at"] = _utc(df["completed_at"])
    props = df["event_properties"].map(lambda s: json.loads(s) if isinstance(s, str) else (s or {}))
    jtype = props.map(lambda p: p.get("bolusType"))
    jopt = props.map(lambda p: p.get("options"))
    std_pct = props.map(lambda p: p.get("standardPercent", 100)).astype(float)
    has_carbs = df["carb_amount"].fillna(0) > 0

    rules = [match_bolus_rule(mappings["bolus_rules"], t, o, c) for t, o, c in zip(jtype, jopt, has_carbs)]
    delivery_type = pd.Series([r["delivery_type"] for r in rules], index=df.index)
    is_automated = pd.Series([bool(r.get("is_automated")) for r in rules], index=df.index)

    delivered = df["insulin_delivered"].astype(float)
    extended = df["bolus_type"].eq("EXTENDED")
    immediate = np.where(extended, np.minimum(delivered, df["insulin_requested"] * std_pct / 100), delivered)
    immediate = pd.Series(immediate, index=df.index).clip(lower=0)

    out = pd.DataFrame({
        "bolus_pk": df["id"].astype("int64"),
        "bolus_id": df["bolus_id"].astype("int64"),
        "requested_at": df["requested_at"],
        "completed_at": df["completed_at"],
        "delivery_type": delivery_type,
        "is_automated": is_automated,
        "insulin_type": ins_cfg["insulin_type"],
        "delivery_shape": np.where(extended, "extended", "standard"),
        "units_requested": df["insulin_requested"].astype(float),
        "units_delivered": delivered,
        "immediate_units": immediate,
        "extended_units": (delivered - immediate).clip(lower=0),
        "completion": df["completion_status"].map(mappings["bolus_completion_status"]).fillna("unknown"),
        "carbs_g": df["carb_amount"].astype(float),
        "carb_ratio_g_per_u": df["carb_ratio"].astype(float) / 1000,  # pump stores g/U x 1000
        "bg_mmol": df["bg"].astype(float).where(df["bg"] > 0) / factor,
        "isf_mmol_per_u": props.map(lambda p: p.get("isf")).astype(float) / factor,
        "target_bg_mmol": props.map(lambda p: p.get("targetBg")).astype(float) / factor,
        "pump_iob_u": props.map(lambda p: p.get("iob")).astype(float),
        "extended_planned_minutes": props.map(lambda p: p.get("duration")).astype(float).where(extended),
        "standard_percent": std_pct,
        "src_bolus_type": df["bolus_type"],
        "src_json_bolus_type": jtype.astype("Int64"),
        "src_json_options": jopt.astype("Int64"),
    })

    # Re-entered meals: a bolus cancelled and entered again repeats the same carbs. Causal rule: an entry with
    # the same grams as an earlier entry within the window is a repeat (counted once).
    win = pd.Timedelta(minutes=ins_cfg["carbs_repeat_window_minutes"])
    carb_rows = out[out["carbs_g"].fillna(0) > 0].sort_values("requested_at")
    repeat = pd.Series(False, index=out.index)
    last_seen: dict[float, pd.Timestamp] = {}
    for i, t_req, g in zip(carb_rows.index, carb_rows["requested_at"], carb_rows["carbs_g"]):
        if g in last_seen and t_req - last_seen[g] <= win:
            repeat[i] = True
        last_seen[g] = t_req
    out["carbs_repeat_entry"] = repeat

    t = "tandem_bolus"
    occurred = out["completed_at"].fillna(out["requested_at"])
    df["_t"] = occurred
    zero = delivered.eq(0)
    incomplete = out["completion"].ne("completed")
    big = delivered > ins_cfg["suspicious_bolus_units"]
    over = delivered > df["insulin_requested"] + 0.05
    unmapped = delivery_type.eq("unknown")
    no_time = out["completed_at"].isna()
    issues = concat_issues([
        issues_frame(t, df[zero], "zero_delivery", "info", "id", "_t", "bolus delivered 0 U"),
        issues_frame(t, df[incomplete], "incomplete_bolus", "info", "id", "_t",
                     "completion=" + out.loc[incomplete, "completion"] + "; delivered "
                     + delivered[incomplete].round(2).astype(str) + " of "
                     + df.loc[incomplete, "insulin_requested"].round(2).astype(str) + " U"),
        issues_frame(t, df[big], "suspicious_bolus_size", "warning", "id", "_t",
                     delivered[big].round(2).astype(str) + " U"),
        issues_frame(t, df[over], "delivered_exceeds_requested", "warning", "id", "_t"),
        issues_frame(t, df[unmapped], "unmapped_bolus_type", "warning", "id", "_t",
                     "bolusType=" + jtype[unmapped].astype(str) + " options=" + jopt[unmapped].astype(str)),
        issues_frame(t, df[no_time], "missing_completion_time", "warning", "id", "_t"),
        issues_frame(t, df[repeat], "repeated_carb_entry", "warning", "id", "_t",
                     "same carbs (" + out.loc[repeat, "carbs_g"].astype(str) + " g) entered again within "
                     f"{ins_cfg['carbs_repeat_window_minutes']} min; counted once"),
    ])
    return out.sort_values("requested_at").reset_index(drop=True), issues


def bolus_deliveries(boluses: pd.DataFrame) -> pd.DataFrame:
    """Boluses as delivery intervals.

    Standard bolus: a point delivery at completed_at (conservative: it is fully
    known by then; standard boluses take < 6 min).
    Extended bolus: an immediate point delivery at requested_at, plus the
    extended part spread over requested_at -> completed_at.
    """
    b = boluses[boluses["units_delivered"] > 0]
    point_time = np.where(b["delivery_shape"].eq("extended"), b["requested_at"],
                          b["completed_at"].fillna(b["requested_at"]))
    point = pd.DataFrame({
        "source_table": "boluses", "source_pk": b["bolus_pk"],
        "start_at": point_time, "end_at": point_time, "available_at": point_time,
        "units": b["immediate_units"], "delivery_type": b["delivery_type"], "basal_change_type": None,
        "is_automated": b["is_automated"], "insulin_type": b["insulin_type"],
    })
    e = b[(b["delivery_shape"] == "extended") & (b["extended_units"] > 0) & b["completed_at"].notna()]
    ext = pd.DataFrame({
        "source_table": "boluses", "source_pk": e["bolus_pk"],
        "start_at": e["requested_at"], "end_at": e["completed_at"], "available_at": e["requested_at"],
        "units": e["extended_units"], "delivery_type": e["delivery_type"], "basal_change_type": None,
        "is_automated": e["is_automated"], "insulin_type": e["insulin_type"],
    })
    out = pd.concat([point[point["units"] > 0], ext], ignore_index=True)
    for c in ("start_at", "end_at", "available_at"):
        out[c] = _utc(out[c])
    return out


def carbs_from_boluses(boluses: pd.DataFrame) -> pd.DataFrame:
    c = boluses[(boluses["carbs_g"].fillna(0) > 0) & ~boluses["carbs_repeat_entry"]]
    return pd.DataFrame({"bolus_pk": c["bolus_pk"], "entered_at": c["requested_at"], "carbs_g": c["carbs_g"]})


# -------------------------------------------------------------------- Basal

def build_basal_segments(raw: pd.DataFrame, settings: dict, mappings: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Turn the basal rate-change log into segments.

    A rate applies from its change until the next change. Segments longer
    than `max_basal_gap_hours` are treated as gaps: delivery is unknown, so
    units = NULL (not zero). The latest segment is open (end_at = NULL).
    Entries sharing a timestamp: the highest sequence number wins.
    """
    ins_cfg = settings["insulin"]
    ct_map = mappings["basal_change_types"]
    df = raw.copy()
    df["changed_at"] = _utc(df["changed_at"])
    props = df["event_properties"].map(lambda s: json.loads(s) if isinstance(s, str) else (s or {}))
    df["change_type_raw"] = props.map(lambda p: json.dumps(sorted(p.get("changeType") or [])))
    df["profile_idp"] = props.map(lambda p: p.get("idp")).astype("Int64")

    df = df.sort_values(["changed_at", "sequence_number"])
    superseded = df.duplicated("changed_at", keep="last")
    t = "tandem_basal"
    issue_frames = [issues_frame(t, df[superseded], "conflicting_basal_entry", "excluded", "id", "changed_at",
                                 "superseded by a later entry at the same time; rate "
                                 + df.loc[superseded, "commanded_rate"].astype(str))]
    df = df[~superseded].copy()

    df["end_at"] = df["changed_at"].shift(-1)
    dur_h = (df["end_at"] - df["changed_at"]).dt.total_seconds() / 3600
    gap = dur_h > ins_cfg["max_basal_gap_hours"]
    units = df["commanded_rate"].astype(float) * dur_h
    units[gap] = np.nan

    # Note: the pump's max_rate is not a hard cap (Control-IQ routinely exceeds it), so only
    # the absolute threshold is used.
    suspicious = df["commanded_rate"] > ins_cfg["suspicious_basal_rate_u_per_h"]
    issue_frames += [
        issues_frame(t, df[gap], "basal_gap", "warning", "id", "changed_at",
                     "no basal record for " + dur_h[gap].round(1).astype(str) + " h; delivery unknown"),
        issues_frame(t, df[suspicious], "suspicious_basal_rate", "warning", "id", "changed_at",
                     df.loc[suspicious, "commanded_rate"].astype(str) + " U/h"),
    ]
    unmapped = ~df["change_type_raw"].isin(ct_map)
    issue_frames.append(issues_frame(t, df[unmapped], "unmapped_basal_change_type", "warning", "id", "changed_at",
                                     df.loc[unmapped, "change_type_raw"]))

    out = pd.DataFrame({
        "basal_pk": df["id"].astype("int64"),
        "start_at": df["changed_at"],
        "end_at": df["end_at"],
        "duration_minutes": dur_h * 60,
        "rate_u_per_h": df["commanded_rate"].astype(float),
        "base_rate_u_per_h": df["base_rate"].astype(float),
        "max_rate_u_per_h": df["max_rate"].astype(float),
        "units": units,
        "change_type": df["change_type_raw"].map(lambda k: ct_map.get(k, ct_map.get("default", "unknown"))),
        "change_type_raw": df["change_type_raw"],
        "profile_idp": df["profile_idp"],
        "is_gap": gap.astype(bool),
    }).reset_index(drop=True)
    return out, concat_issues(issue_frames)


def basal_deliveries(segments: pd.DataFrame, insulin_type: str) -> pd.DataFrame:
    s = segments[segments["units"].notna() & segments["end_at"].notna()]
    return pd.DataFrame({
        "source_table": "basal_segments", "source_pk": s["basal_pk"],
        "start_at": s["start_at"], "end_at": s["end_at"],
        "available_at": s["start_at"],  # the commanded rate is known when it starts
        "units": s["units"], "delivery_type": "basal", "basal_change_type": s["change_type"],
        "is_automated": s["change_type"].str.startswith("automated"), "insulin_type": insulin_type,
    })


# ------------------------------------------------------------------ Helpers

def spread_to_bins(df: pd.DataFrame, start_col: str, end_col: str, units_col: str,
                   freq: str = "1h") -> pd.DataFrame:
    """Split interval deliveries across fixed UTC bins, proportionally to overlap.

    Point deliveries (start == end) go wholly into their bin. Returns columns
    bin_start, units, plus every other input column. Total units are preserved.
    """
    d = df[df[units_col].notna()].reset_index(drop=True)
    if d.empty:
        return d.assign(bin_start=pd.Series(dtype="datetime64[ns, UTC]"))
    step = pd.Timedelta(freq)
    start, end = d[start_col], d[end_col]
    first = start.dt.floor(freq)
    n = np.maximum(1, np.ceil((end - first) / step).astype(int).to_numpy())
    idx = np.repeat(np.arange(len(d)), n)
    k = np.concatenate([np.arange(c) for c in n])
    rep = d.iloc[idx].reset_index(drop=True)
    bin_start = first.iloc[idx].reset_index(drop=True) + pd.to_timedelta(k * step.value, unit="ns")
    s, e = rep[start_col], rep[end_col]
    duration = (e - s).dt.total_seconds()
    overlap = (np.minimum(e, bin_start + step) - np.maximum(s, bin_start)).dt.total_seconds().clip(lower=0)
    frac = np.where(duration > 0, overlap / duration.where(duration > 0, 1), 1.0)
    rep["bin_start"] = bin_start
    rep[units_col] = rep[units_col] * frac
    return rep[rep[units_col] > 0].reset_index(drop=True)
