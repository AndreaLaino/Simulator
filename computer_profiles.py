# computer_profiles.py
from __future__ import annotations
from typing import Dict

COMPUTER_PROFILES: Dict[str, dict] = {
    "PC_low": {
        "standby": 35.0,
        "target_mean": 40.0,
        "profile": {
            0: 30.0,
            15: 35.0,
            30: 40.0,
            45: 38.0,
            60: 42.0,
            75: 39.0,
            90: 41.0,
            105: 40.0,
        },
    },

    "PC_medium": {
        "standby": 60.0,
        "target_mean": 70.0,
        "profile": {
            0: 55.0,
            15: 65.0,
            30: 75.0,
            45: 68.0,
            60: 72.0,
            75: 70.0,
            90: 69.0,
            105: 71.0,
        },
    },

    "PC_high": {
        "standby": 103.5,
        "target_mean": 100.0,
        "profile": {
            0: 90.4,
            13: 90.9,
            26: 52.1,
            65: 73.5,
            78: 106.5,
            101: 111.5,
            114: 108.7,
            127: 103.2,
            150: 100.9,
            173: 102.7,
            196: 103.8,
            205: 105.3,
            218: 104.6,
            231: 103.1,
            245: 103.5,
        },
    },
}