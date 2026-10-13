import numpy as np
import pandas as pd
import pytest

from src import preprocessing as pp
from tests.conftest import T0, basal_row, bolus_row, tandem_cgm_row

MIN = pd.Timedelta(minutes=1)


# ------------------------------------------------------------- tandem CGM

def test_backfill_time_is_rebuilt_from_nearest_live_clock_offset(settings, mappings):
    raw = pd.DataFrame([
        tandem_cgm_row(1, T0, 100, egv=1000),
        tandem_cgm_row(2, T0 + 5 * MIN, 105, egv=1300),
        # measured 5 min after row 2 but logged 20 min late
        tandem_cgm_row(3, T0 + 30 * MIN, 110, egv=1600, backfill=True),
    ])
    out, issues = pp.clean_tandem_cgm(raw, settings, mappings)
    bf = out[out["source_pk"] == 3].iloc[0]
    assert bf["measured_at"] == T0 + 10 * MIN
    assert bf["available_at"] == T0 + 30 * MIN  # arrival time kept for leakage control
    assert bf["time_corrected"] and bf["is_backfill"]
    assert "backfill_time_corrected" in set(issues["issue_type"])


def test_live_readings_keep_reading_at(settings, mappings):
    raw = pd.DataFrame([tandem_cgm_row(1, T0, 100, egv=1000)])
    out, _ = pp.clean_tandem_cgm(raw, settings, mappings)
    assert out.loc[0, "measured_at"] == T0 == out.loc[0, "available_at"]
    assert not out.loc[0, "time_corrected"]


def test_backfill_without_nearby_live_reading_is_flagged(settings, mappings):
    far = settings["cgm"]["backfill_offset_tolerance_seconds"] + 10
    raw = pd.DataFrame([
        tandem_cgm_row(1, T0, 100, egv=1000),
        tandem_cgm_row(2, T0 + pd.Timedelta(hours=5), 110, egv=1000 + far, backfill=True),
    ])
    out, issues = pp.clean_tandem_cgm(raw, settings, mappings)
    assert out.loc[out["source_pk"] == 2, "measured_at"].iloc[0] == T0 + pd.Timedelta(hours=5)
    assert "backfill_time_uncorrected" in set(issues["issue_type"])


def test_duplicate_prefers_live_over_backfill_and_is_not_deleted(settings, mappings):
    raw = pd.DataFrame([
        tandem_cgm_row(1, T0 + 20 * MIN, 120, egv=1000, backfill=True, seq=50),
        tandem_cgm_row(2, T0, 100, egv=1000, seq=10),
    ])
    out, issues = pp.clean_tandem_cgm(raw, settings, mappings)
    assert len(out) == 2  # nothing deleted
    kept = out[out["exclude_reason"].isna()]
    assert list(kept["source_pk"]) == [2]
    dup = issues[issues["issue_type"] == "duplicate_reading"]
    assert list(dup["source_pk"]) == [1]
    assert dup["detail"].iloc[0] == "value conflicts with kept reading"


def test_censoring_and_unit_conversion(settings, mappings):
    f = settings["units"]["mgdl_per_mmol"]
    raw = pd.DataFrame([
        tandem_cgm_row(1, T0, 30, egv=1000, status=2),       # LOW
        tandem_cgm_row(2, T0 + 5 * MIN, 420, egv=1300, status=1),  # HIGH
        tandem_cgm_row(3, T0 + 10 * MIN, 180, egv=1600),
    ])
    out, _ = pp.clean_tandem_cgm(raw, settings, mappings)
    out = out.set_index("source_pk")
    assert out.loc[1, "censored"] == "low" and out.loc[1, "glucose_mgdl"] == settings["cgm"]["sensor_low_limit_mgdl"]
    assert out.loc[1, "sgv_raw"] == 30
    assert out.loc[2, "censored"] == "high" and out.loc[2, "glucose_mgdl"] == settings["cgm"]["sensor_high_limit_mgdl"]
    assert out.loc[3, "glucose_mmol"] == pytest.approx(180 / f)
    assert out.loc[3, "censored"] is None


def test_impossible_value_is_excluded_unless_censored(settings, mappings):
    raw = pd.DataFrame([tandem_cgm_row(1, T0, 0, egv=1000), tandem_cgm_row(2, T0 + 5 * MIN, 0, egv=1300, status=2)])
    out, _ = pp.clean_tandem_cgm(raw, settings, mappings)
    out = out.set_index("source_pk")
    assert out.loc[1, "exclude_reason"] == "impossible_value"
    assert pd.isna(out.loc[2, "exclude_reason"])


def test_trend_and_rate_mapping(settings, mappings):
    raw = pd.DataFrame([tandem_cgm_row(1, T0, 100, egv=1000, trend=1, rate=18),
                        tandem_cgm_row(2, T0 + 5 * MIN, 90, egv=1300, trend=7, rate=-18)])
    out, _ = pp.clean_tandem_cgm(raw, settings, mappings)
    assert list(out["trend_ordinal"]) == [3, -3]
    assert out.loc[0, "rate_mmol_per_min"] == pytest.approx(1.8 / settings["units"]["mgdl_per_mmol"])


def test_suspicious_rate_flagged_only_at_normal_spacing(settings, mappings):
    raw = pd.DataFrame([
        tandem_cgm_row(1, T0, 100, egv=1000),
        tandem_cgm_row(2, T0 + 5 * MIN, 180, egv=1300),     # +4.4 mmol/L in 5 min: suspicious
        tandem_cgm_row(3, T0 + 6 * MIN, 150, egv=1360),     # 1 min later: rate not checked
    ])
    out, _ = pp.clean_tandem_cgm(raw, settings, mappings)
    assert list(out["is_suspicious"]) == [False, True, False]
    assert out["exclude_reason"].isna().all()  # flagged, not excluded


# ---------------------------------------------------------- Nightscout/merge

def ns_raw(rows):
    return pd.DataFrame([{"id": i, "sgv": sgv, "ns_time": int(t.timestamp() * 1000), "ns_datetime": t, "trend": 4,
                          "utcoffset": 0, "systime": t} for i, t, sgv in rows])


def test_nightscout_low_high_and_duplicates(settings, mappings):
    raw = ns_raw([(1, T0, 39), (2, T0 + 5 * MIN, 401), (3, T0 + 5 * MIN, 401), (4, T0 + 10 * MIN, 120)])
    out, _ = pp.clean_nightscout_cgm(raw, settings, mappings)
    out = out.set_index("source_pk")
    assert out.loc[1, "censored"] == "low" and out.loc[2, "censored"] == "high"
    assert out.loc[2, "exclude_reason"] == "duplicate" and pd.isna(out.loc[3, "exclude_reason"])
    assert (out["available_at"] == out["measured_at"]).all()


def test_merge_fills_only_tandem_gaps(settings, mappings):
    tandem, _ = pp.clean_tandem_cgm(pd.DataFrame([tandem_cgm_row(1, T0, 100, egv=1000)]), settings, mappings)
    ns, _ = pp.clean_nightscout_cgm(ns_raw([(1, T0 + pd.Timedelta(seconds=60), 101),     # overlaps tandem
                                            (2, T0 + 20 * MIN, 130)]), settings, mappings)  # in a tandem gap
    merged = pp.merge_cgm(tandem, ns, 150)
    assert list(merged["reading_id"]) == ["tandem:1", "nightscout:2"]


# ------------------------------------------------------------------ boluses

@pytest.mark.parametrize("json_type,options,carbs,expected,automated", [
    (1, 0, 50.0, "bolus", False),
    (1, 0, 0.0, "bolus", False),
    (2, 3, 0.0, "automated_correction", True),
    (3, 4, 40.0, "bolus", False),
    (3, 4, 0.0, "manual_correction", False),
    (9, 9, 0.0, "unknown", False),
])
def test_bolus_mapping(settings, mappings, json_type, options, carbs, expected, automated):
    out, issues = pp.clean_boluses(pd.DataFrame([bolus_row(1, T0, 2.0, json_type, options, carbs)]),
                                   settings, mappings)
    assert out.loc[0, "delivery_type"] == expected
    assert out.loc[0, "is_automated"] == automated
    assert ("unmapped_bolus_type" in set(issues["issue_type"])) == (expected == "unknown")


def test_bolus_unit_conversions(settings, mappings):
    out, _ = pp.clean_boluses(pd.DataFrame([bolus_row(1, T0, 2.0)]), settings, mappings)
    f = settings["units"]["mgdl_per_mmol"]
    assert out.loc[0, "carb_ratio_g_per_u"] == 10.0
    assert out.loc[0, "bg_mmol"] == pytest.approx(180 / f)
    assert out.loc[0, "isf_mmol_per_u"] == pytest.approx(90 / f)


def test_extended_bolus_split_and_deliveries(settings, mappings):
    end = T0 + pd.Timedelta(hours=1)
    raw = pd.DataFrame([bolus_row(1, T0, 3.0, json_type=1, options=1, kind="EXTENDED", requested=4.0, std_pct=50,
                                  completed_at=end, status=1)])
    b, issues = pp.clean_boluses(raw, settings, mappings)
    assert b.loc[0, "immediate_units"] == pytest.approx(2.0)
    assert b.loc[0, "extended_units"] == pytest.approx(1.0)
    assert "incomplete_bolus" in set(issues["issue_type"])
    d = pp.bolus_deliveries(b).sort_values("end_at").reset_index(drop=True)
    assert len(d) == 2
    assert d.loc[0, "start_at"] == d.loc[0, "end_at"] == T0 and d.loc[0, "units"] == pytest.approx(2.0)
    assert d.loc[1, "start_at"] == T0 and d.loc[1, "end_at"] == end and d.loc[1, "units"] == pytest.approx(1.0)
    assert d["units"].sum() == pytest.approx(b["units_delivered"].sum())


def test_standard_bolus_delivered_at_completion_and_zero_skipped(settings, mappings):
    raw = pd.DataFrame([bolus_row(1, T0, 2.0), bolus_row(2, T0 + MIN, 0.0, status=0)])
    b, issues = pp.clean_boluses(raw, settings, mappings)
    d = pp.bolus_deliveries(b)
    assert len(d) == 1 and d.iloc[0]["start_at"] == T0 + 2 * MIN
    assert "zero_delivery" in set(issues["issue_type"])


def test_carbs_from_boluses(settings, mappings):
    raw = pd.DataFrame([bolus_row(1, T0, 4.0, carbs=60.0), bolus_row(2, T0 + MIN, 1.0)])
    b, _ = pp.clean_boluses(raw, settings, mappings)
    c = pp.carbs_from_boluses(b)
    assert list(c["carbs_g"]) == [60.0] and c.iloc[0]["entered_at"] == T0


# -------------------------------------------------------------------- basal

def test_basal_segments_units_gap_and_conflicts(settings, mappings):
    H = pd.Timedelta(hours=1)
    raw = pd.DataFrame([
        basal_row(1, T0, 0.5),
        basal_row(2, T0 + 2 * H, 1.0, seq=2),
        basal_row(3, T0 + 2 * H, 0.0, change_type=(4,), seq=3),   # same instant, later sequence wins
        basal_row(4, T0 + 3 * H, 0.4),
        basal_row(5, T0 + 13 * H, 0.4),                            # 10 h after previous: gap
    ])
    seg, issues = pp.build_basal_segments(raw, settings, mappings)
    assert list(seg["basal_pk"]) == [1, 3, 4, 5]
    assert seg.loc[0, "units"] == pytest.approx(1.0)
    assert seg.loc[1, "units"] == 0 and seg.loc[1, "change_type"] == "suspend"
    assert seg.loc[2, "is_gap"] and np.isnan(seg.loc[2, "units"])
    assert pd.isna(seg.loc[3, "end_at"])  # open latest segment
    types = issues.groupby("issue_type")["source_pk"].apply(list).to_dict()
    assert types["conflicting_basal_entry"] == [2]
    assert types["basal_gap"] == [4]
    d = pp.basal_deliveries(seg, "rapid_acting")
    assert list(d["source_pk"]) == [1, 3]  # gap and open segments carry no known units


# ------------------------------------------------------------------ helpers

def test_spread_to_bins_conserves_units_and_splits_proportionally():
    df = pd.DataFrame({
        "start_at": [T0 + 30 * MIN, T0 + 10 * MIN],
        "end_at": [T0 + 90 * MIN, T0 + 10 * MIN],       # interval across an hour boundary; a point delivery
        "units": [3.0, 1.0],
        "kind": ["basal", "bolus"],
    })
    b = pp.spread_to_bins(df, "start_at", "end_at", "units", "1h")
    assert b["units"].sum() == pytest.approx(4.0)
    basal = b[b["kind"] == "basal"].set_index("bin_start")["units"]
    assert basal[T0] == pytest.approx(1.5) and basal[T0 + 60 * MIN] == pytest.approx(1.5)
    assert b[b["kind"] == "bolus"]["bin_start"].tolist() == [T0]


def test_repeated_carb_entries_are_counted_once(settings, mappings):
    raw = pd.DataFrame([
        bolus_row(1, T0, 0.0, carbs=181.0, status=1),            # cancelled
        bolus_row(2, T0 + 3 * MIN, 5.0, carbs=181.0),             # same meal re-entered
        bolus_row(3, T0 + 120 * MIN, 5.0, carbs=181.0),           # later meal, same size: a new meal
        bolus_row(4, T0 + 125 * MIN, 1.0, carbs=20.0),
    ])
    b, issues = pp.clean_boluses(raw, settings, mappings)
    assert list(b["carbs_repeat_entry"]) == [False, True, False, False]
    assert list(issues.loc[issues["issue_type"] == "repeated_carb_entry", "source_pk"]) == [2]
    assert pp.carbs_from_boluses(b)["carbs_g"].sum() == 181 + 181 + 20
