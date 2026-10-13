import json

import pandas as pd
import pytest

from src import config

T0 = pd.Timestamp("2026-06-01 12:00", tz="UTC")


@pytest.fixture
def settings():
    return config.settings()


@pytest.fixture
def mappings():
    return config.mappings()


def tandem_cgm_row(id, reading_at, sgv, egv, backfill=False, status=0, trend=4, rate=0, seq=None):
    return {
        "id": id, "device_assignment_id": "dev", "sequence_group": 1, "sequence_number": seq if seq is not None else id,
        "reading_at": reading_at, "sgv": float(sgv), "trend": trend, "rate": float(rate),
        "glucose_value_status": status,
        "event_properties": json.dumps({"egvTimeStamp": egv, "cgmDataType": [1] if backfill else [0]}),
    }


def bolus_row(id, requested_at, delivered, json_type=1, options=0, carbs=0.0, kind="STANDARD", requested=None,
              std_pct=100, completed_at=None, status=3):
    return {
        "id": id, "device_assignment_id": "dev", "bolus_id": id, "bolus_type": kind,
        "requested_at": requested_at, "completed_at": completed_at or requested_at + pd.Timedelta(minutes=2),
        "insulin_requested": requested if requested is not None else delivered, "insulin_delivered": delivered,
        "food_bolus_size": 0.0, "correction_bolus_size": 0.0, "correction_included": False, "carb_amount": carbs,
        "carb_ratio": 10000.0, "bg": 180.0, "completion_status": status,
        "event_properties": json.dumps({"bolusType": json_type, "options": options, "standardPercent": std_pct,
                                        "isf": 90, "targetBg": 110, "iob": 1.5}),
    }


def basal_row(id, changed_at, rate, change_type=(0,), seq=None, base=0.4, max_rate=0.6):
    return {
        "id": id, "device_assignment_id": "dev", "sequence_group": 1, "sequence_number": seq if seq is not None else id,
        "changed_at": changed_at, "commanded_rate": rate, "base_rate": base, "max_rate": max_rate,
        "event_properties": json.dumps({"changeType": list(change_type), "idp": 0}),
    }
