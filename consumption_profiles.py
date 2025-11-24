#consumption_profiles.py
from __future__ import annotations

from typing import Dict, Optional
import pandas as pd

import smartmeter                      
from computer_profiles import COMPUTER_PROFILES 

consumption_profiles = {
    "Fridge": {
        "standby": 23, # on the left the value of the minutes, on the right the value of the power consumed
        "profile": {
            0: 74.7,
            16: 70.6,
            33: 70.6,
            49: 99.7,
            65: 99.7,
            81: 99.7,
            98: 74.8,
            114: 24.0,
            130: 24.0,
            146: 90.1,
            163: 90.1,
            179: 82.9
        }
    },

    "Washing_Machine": {
        "standby": 0,
        "profile": {
            0: 3.0,
            13: 687.6,
            26: 2094.3,
            39: 102.9,
            52: 100.3,
            65: 108.3,
            78: 138.7,
            91: 255.0
        }
    },

    "Oven": {
        "standby": 0,
        "profile": {
            0: 942.8,
            3: 995.3,
            6: 916.6,
            9: 947.7
        }
    },

    "Computer": {
        "standby": 103.5,
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
            245: 103.5  # return to standby
        }
    },

    "Dishwasher": {
        "standby": 0,
        "profile": {
            0: 67.1,
            13: 1716.1,
            26: 151.2,
            39: 66.5,
            52: 1966.7,
            65: 7.8,
            78: 4.6
        }
    },

    "Coffee_Machine": {
        "standby": 0,
        "profile": {
            0: 1200.0,
            1: 700.0,
            2: 200.0
        }
    }
}

_SELECTED_PC_PROFILE_BY_DEVICE: Dict[str, Optional[str]] = {}
DEVICE_ID_BY_SIM_NAME: Dict[str, str] = {
    "pc": "sm_pc",
    "sm_pc": "sm_pc",   
}

def interpolated_consumption(profile, minutes, standby):
    keys = sorted(profile)
    if not keys:
        return standby
    if minutes <= keys[0]:
        return profile[keys[0]]
    elif minutes >= keys[-1]:
        return profile[keys[-1]]
    else:
        for i in range(len(keys) - 1):
            t1, t2 = keys[i], keys[i + 1]
            if t1 <= minutes < t2:
                c1, c2 = profile[t1], profile[t2]
                factor = (minutes - t1) / (t2 - t1)
                return c1 + (c2 - c1) * factor
    return profile[keys[0]]


def consumption_step(profile: dict, minutes: float, standby: float, repeat: bool = False, start_from_standby: bool = True) -> float:

    if not profile:
        return standby
    keys = sorted(profile)
    duration = keys[-1]

    t = minutes % duration if repeat and duration > 0 else minutes

    if t < keys[0]:
        return profile[keys[0]]

    last_key = keys[0]
    for k in keys:
        if t < k:
            return profile[last_key]
        last_key = k
    return profile[keys[-1]] if repeat else profile[keys[-1]]

from datetime import datetime  # if not already imported

def get_device_consumption(device_name, device_type, current_timestamp, active_cycles, device_state=1):
    """
    - If device_state == 0 → 0 W.
    - For 'Computer' type: pick the PC profile whose target_mean W
      is closest to the real mean W measured by the smartmeter.
    - For other types: use static profiles in consumption_profiles.
    """
    if device_state == 0:
        return 0.0

    # ---- choose base profile dict depending on device_type ----
    if device_type == "Computer":
        # try to pick a specific computer template based on wattage
        pc_profile_name = _choose_pc_profile_for_device(device_name)
        if pc_profile_name and pc_profile_name in COMPUTER_PROFILES:
            base = COMPUTER_PROFILES[pc_profile_name]
        else:
            # fallback: generic "Computer" template (if you keep it)
            base = consumption_profiles.get("Computer")
    else:
        base = consumption_profiles.get(device_type)

    if not base:
        return 0.0

    standby = base["standby"]
    prof_det = base["profile"]

    repeat_by_type = {
        "Fridge": True,
        "Washing_Machine": False,
        "Dishwasher": False,
        "Coffee_Machine": False,
        "Oven": False,
        "Computer": False,  # or True if you want looping PC patterns
    }
    repeat = repeat_by_type.get(device_type, False)

    if device_name in active_cycles:
        start_time, _type = active_cycles[device_name]
        elapsed_min = (current_timestamp - start_time).total_seconds() / 60.0
        return consumption_step(prof_det, elapsed_min, standby, repeat=repeat, start_from_standby=True)
    else:
        keys = sorted(prof_det)
        return prof_det[keys[0]] if keys else standby


#Returns the device_id to look for in smartmeter CSVs.
def _csv_id_for_device(device_name: str) -> str:
    return DEVICE_ID_BY_SIM_NAME.get(device_name, device_name)

#Load all smartmeter CSVs for this device_id and return mean power (W)
def _real_mean_power_for_device(device_name: str, logs_dir: str = "logs") -> Optional[float]:
    csv_id = _csv_id_for_device(device_name)
    df = smartmeter.load_power_by_device_id_any_csv(csv_id, logs_dir=logs_dir)
    if df.empty:
        return None
    # 'value' column contains power in W
    return float(df["value"].mean())

#Pick the computer profile whose target_mean is closest to the real mean power of this device (from smartmeter logs).
def _choose_pc_profile_for_device(device_name: str) -> Optional[str]:
    if device_name in _SELECTED_PC_PROFILE_BY_DEVICE:
        return _SELECTED_PC_PROFILE_BY_DEVICE[device_name]

    mean_power = _real_mean_power_for_device(device_name)
    print(f"[PC profile] device={device_name}, real mean power={mean_power}")  # DEBUG

    if mean_power is None:
        _SELECTED_PC_PROFILE_BY_DEVICE[device_name] = None
        return None

    best_name = None
    best_delta = None

    for name, prof in COMPUTER_PROFILES.items():
        target = float(prof.get("target_mean", prof.get("standby", 0.0)))
        delta = abs(target - mean_power)
        print(f"  candidate {name}: target_mean={target}, delta={delta}")     # DEBUG
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_name = name

    print(f"[PC profile] chosen {best_name} for {device_name}\n")              # DEBUG
    _SELECTED_PC_PROFILE_BY_DEVICE[device_name] = best_name
    return best_name