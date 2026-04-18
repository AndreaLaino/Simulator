import tkinter as tk
from tkinter import simpledialog, messagebox
from tkinter import ttk
from utils import draw_sensor, calculate_distance, update_temperature_sensor_color
from device import devices
from datetime import datetime
from consumption_profiles import get_device_consumption, consumption_profiles
from read import read_sensors as sensors_file
from read import read_devices as devices_file
import os, json
import math
import pickle
from pathlib import Path
from bisect import bisect_right
from dhtlogger import load_temp_by_label_any_csv, load_temp_by_gpio_any_csv
import pandas as pd
from collections import deque
from typing import Optional
from models import Sensor, Device
from house_state import HouseState

TEMP_RECENT: dict[str, deque] = {}
TEMP_GREEN_UNTIL: dict[str, float] = {}
TEMP_BASELINE: dict[str, float] = {}

_BASE_DIR = Path(__file__).resolve().parent
LLM_PROFILE_CATALOG_PATH = _BASE_DIR / "LLM" / "smartmeter" / "llm_smartmeter_profiles.json"
LLM_PROFILE_CATALOG_LEGACY_PATH = _BASE_DIR / "llm_smartmeter_profiles.json"
LLM_PROFILE_CATALOG_CACHE: Optional[dict] = None
LLM_PROFILE_CATALOG_MTIME: Optional[float] = None
LLM_PROFILE_CATALOG_ACTIVE_PATH: Optional[Path] = None
LLM_CYCLE_CURVE_CACHE: dict[tuple[str, int], Optional[tuple[list[float], list[float]]]] = {}
LLM_SENSOR_ON_START: dict[str, datetime] = {}

sensors = []
add_point_enabled = False

SENSOR_MAP_PATH = "sensor_map.json"

# cache: per sensor -> (datetimes_list, values_list_C)
TEMP_SERIES: dict[str, Optional[tuple[list[datetime], list[float]]]] = {}
# simulated time (in delta_seconds units) per sensor
TEMP_SIM_MIN: dict[str, float] = {}


class TemperatureSensorAdapter:
    """Pure adapter: translate HouseState runtime into compute_temperature inputs."""

    def update(
        self,
        state: HouseState,
        sensor: Sensor,
        *,
        heating_factor: float | None = None,
        delta_seconds: float | None = None,
        current_datetime=None,
        active_devices=None,
        render: bool = True,
    ):
        runtime = state.runtime_view(
            heating_factor=heating_factor,
            delta_seconds=delta_seconds,
            current_datetime=current_datetime,
            devices=active_devices,
        )
        heating_factor = float(runtime.get("heating_factor", 0.0) or 0.0)
        delta_seconds = float(runtime.get("delta_seconds", 1.0) or 1.0)
        current_datetime = runtime.get("current_datetime")
        active_devices = runtime.get("devices")

        new_state = compute_temperature(
            sensor,
            heating_factor,
            delta_seconds,
            current_datetime,
            active_devices,
        )
        return sensor.name, new_state


class PIRSensorAdapter:
    """Pure adapter for PIR state transitions."""

    def update(self, state: HouseState, sensor: Sensor, new_state=None, *, render: bool = True):
        current_state = float(sensor.state)
        resolved_state = 1.0 if current_state == 0.0 else 0.0 if new_state is None else float(new_state)
        return sensor.name, resolved_state


class SmartMeterSensorAdapter:
    """Pure adapter: translate HouseState runtime into Smart Meter inputs."""

    def update(
        self,
        state: HouseState,
        sensor: Sensor,
        *,
        devices_list=None,
        delta_seconds: float | None = None,
        current_datetime=None,
        render: bool = True,
    ):
        runtime = state.runtime_view(
            devices=devices_list,
            delta_seconds=delta_seconds,
            current_datetime=current_datetime,
        )
        devices_list = runtime.get("devices")
        if devices_list is None:
            raise RuntimeError("SmartMeterSensorAdapter requires runtime['devices'] or an explicit devices_list")
        delta_seconds = float(runtime.get("delta_seconds", 1.0) or 1.0)
        current_datetime = runtime.get("current_datetime")
        cycles_store = state.active_cycles()

        consumption = compute_smartmeter_consumption(
            sensor,
            devices_list,
            delta_seconds,
            current_datetime,
            cycles_store,
        )
        return sensor.name, consumption


def is_temperature_sensor_changing(sensor_name: str) -> bool:
    sim_min = float(TEMP_SIM_MIN.get(sensor_name, 0.0))
    green_until = float(TEMP_GREEN_UNTIL.get(sensor_name, 0.0))
    return sim_min <= green_until


class WeightSensorAdapter:
    """Pure adapter for Weight sensor transitions."""

    def update(self, state: HouseState, sensor: Sensor, new_state, *, render: bool = True):
        return sensor.name, float(new_state)


class SwitchSensorAdapter:
    """Pure adapter for Switch sensor transitions."""

    def update(self, state: HouseState, sensor: Sensor, door_state, *, render: bool = True):
        return sensor.name, _normalize_switch_state(door_state)

def _load_sensor_map(path: str = SENSOR_MAP_PATH) -> dict:
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[WARN] cannot load {path}: {e}")
    return {}


def _sanitize(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in (name or "").strip())

def _load_temp_series_for_sensor(sensor_name: str):
    """
    Load the DHT temperature series for this sensor.
    Uses load_temp_by_label_any_csv(sensor_name), the same mechanism
    used for the "real" graphs.

    Returns:
        (times, values)
    where:
        times  = minutes relative to the first sample (float)
        values = temperature in C (float)
    or None if no data is available.
    """
    if not sensor_name:
        TEMP_SERIES[sensor_name] = None
        return None

    # cache already loaded
    if sensor_name in TEMP_SERIES:
        return TEMP_SERIES[sensor_name]

    # 1) try by label
    df = None
    try:
        df = load_temp_by_label_any_csv(sensor_name)
    except Exception as e:
        print(f"[TEMP] load_temp_by_label_any_csv failed for {sensor_name}: {e}")

    # 2) fallback to GPIO
    if (df is None or df.empty or "value" not in df.columns):
        mapping = _load_sensor_map()
        cfg = mapping.get(sensor_name, {})
        if isinstance(cfg, dict) and cfg.get("by") == "dht":
            gpio = cfg.get("gpio")
            if gpio is not None:
                try:
                    df = load_temp_by_gpio_any_csv(int(gpio))
                except Exception as e:
                    print(f"[TEMP] load_temp_by_gpio_any_csv failed for {sensor_name}: {e}")

    if df is None or df.empty or "value" not in df.columns:
        print(f"[TEMP] no series found for {sensor_name}")
        TEMP_SERIES[sensor_name] = None
        return None

    if df is None or df.empty or "value" not in df.columns:
        print(f"[TEMP] no series found for {sensor_name}")
        TEMP_SERIES[sensor_name] = None
        return None

    # keep only valid values (drop unreadable entries)
    df = df.dropna(subset=["value"])
    if df.empty:
        print(f"[TEMP] only NaN values for {sensor_name}")
        TEMP_SERIES[sensor_name] = None
        return None

    # ensure time ordering
    if not isinstance(df.index, pd.DatetimeIndex):
        try:
            df.index = pd.to_datetime(df.index, errors="coerce")
        except Exception as e:
            print(f"[TEMP] cannot convert index to datetime for {sensor_name}: {e}")
    df = df.sort_index()

    if df.empty:
        TEMP_SERIES[sensor_name] = None
        return None

    # Store actual datetime objects (aligned to real timestamps, not relative minutes)
    datetimes = df.index.to_list()
    values = df["value"].astype(float).to_list()

    TEMP_SERIES[sensor_name] = (datetimes, values)
    print(
        f"[TEMP] {sensor_name}: {len(datetimes)} samples "
        f"from {datetimes[0]} to {datetimes[-1]}"
    )
    return TEMP_SERIES[sensor_name]

def _get_intraday_pattern(sensor_name: str, time_of_day_minutes: float, window_days: int = 7) -> Optional[float]:
    """
    Use historical data to find the intraday pattern.

    Looks across previous days for the typical value at this time of day,
    and returns a weighted average (more recent = higher weight).

    Args:
        sensor_name: sensor name
        time_of_day_minutes: minutes since midnight (0-1440)
        window_days: how many previous days to analyze

    Returns:
        predicted value or None if no data is available
    """
    df = None
    mapping = _load_sensor_map()
    cfg = mapping.get(sensor_name, {})
    
    # Try to load data
    if isinstance(cfg, dict) and cfg.get("by") == "dht":
        gpio = cfg.get("gpio")
        if gpio is not None:
            try:
                df = load_temp_by_gpio_any_csv(int(gpio))
            except Exception:
                pass
    
    if df is None or df.empty:
        try:
            df = load_temp_by_label_any_csv(sensor_name)
        except Exception:
            pass
    
    if df is None or df.empty or "value" not in df.columns:
        return None
    
    df = df.dropna(subset=["value"])
    if df.empty:
        return None
    
    # Add hour-of-day (minutes since midnight) and day columns
    if not isinstance(df.index, pd.DatetimeIndex):
        return None
    
    df_copy = df.copy()
    df_copy["hour_of_day"] = (df_copy.index.hour * 60 + df_copy.index.minute).astype(float)
    df_copy["day"] = df_copy.index.date
    
    # Find similar values (within ±30 minutes)
    tolerance_min = 30
    candidates = df_copy[
        (df_copy["hour_of_day"] >= time_of_day_minutes - tolerance_min) &
        (df_copy["hour_of_day"] <= time_of_day_minutes + tolerance_min)
    ]
    
    if candidates.empty:
        return None
    
    # Weighted average (more recent days have higher weight)
    unique_days = candidates["day"].unique()
    if len(unique_days) > window_days:
        unique_days = unique_days[-window_days:]
    
    weighted_sum = 0.0
    weight_total = 0.0
    
    for idx, day in enumerate(unique_days):
        day_data = candidates[candidates["day"] == day]["value"]
        if not day_data.empty:
            day_mean = float(day_data.mean())
            # Weight: more recent days have higher weight (exponential)
            weight = 2.0 ** (idx - len(unique_days) + 1)
            weighted_sum += day_mean * weight
            weight_total += weight
    
    if weight_total > 0:
        return weighted_sum / weight_total
    
    return None


def get_replay_temperature(sensor_name: str, current_datetime: Optional[datetime] = None):
    """
    Temperature replay based on historical data, aligned to real time.

    Logic:
    - For any datetime, search for data from that SAME DATE in the CSV
    - If that date has data -> interpolate the time within that day
    - If that date has NO data (future) -> use intraday pattern from other days
    
    Args:
        sensor_name: name of the sensor
        current_datetime: current datetime to look up (uses date + time of day)
    
    Returns:
        Temperature value or None if no data is available
    """
    # Handle None or missing datetime -> fallback to model-only
    if current_datetime is None:
        return None
    
    series = _load_temp_series_for_sensor(sensor_name)
    if not series:
        return None

    datetimes, values = series
    if not datetimes or not values:
        return None

    # Convert all to timezone-naive for comparison
    current_dt = current_datetime
    if current_dt.tzinfo is not None:
        current_dt = current_dt.replace(tzinfo=None)

    # Search for data from the SAME DATE in the CSV
    target_date = current_dt.date()
    
    # Find all data points from this date
    same_date_indices = [i for i, dt in enumerate(datetimes) if dt.date() == target_date]
    
    if same_date_indices:
        # This date HAS data in the CSV -> interpolate within that day
        first_idx = same_date_indices[0]
        last_idx = same_date_indices[-1]
        
        dt_first = datetimes[first_idx]
        dt_last = datetimes[last_idx]
        
        # Before first sample of this day
        if current_dt < dt_first:
            return float(values[first_idx])
        
        # After last sample of this day
        if current_dt > dt_last:
            # Extrapolate damped from today's data
            v_prev = float(values[last_idx - 1]) if last_idx > first_idx else float(values[last_idx])
            v_last = float(values[last_idx])
            slope = v_last - v_prev
            time_since_last = (current_dt - dt_last).total_seconds() / 60.0
            damped_slope = slope * (0.95 ** time_since_last)
            return max(15.0, min(40.0, v_last + damped_slope))
        
        # Within range: interpolate
        for i in range(len(same_date_indices) - 1):
            idx1 = same_date_indices[i]
            idx2 = same_date_indices[i + 1]
            dt1 = datetimes[idx1]
            dt2 = datetimes[idx2]
            
            if dt1 <= current_dt <= dt2:
                v1 = float(values[idx1])
                v2 = float(values[idx2])
                
                if dt2 == dt1:
                    return v1
                
                # Linear interpolation
                time_diff = (dt2 - dt1).total_seconds()
                time_into = (current_dt - dt1).total_seconds()
                alpha = time_into / time_diff
                return v1 + alpha * (v2 - v1)
        
        # Last sample of the day
        return float(values[last_idx])
    
    else:
        # This date has NO data -> use intraday pattern from other days
        time_of_day = (current_dt.hour * 60 + current_dt.minute)
        predicted = _get_intraday_pattern(sensor_name, time_of_day)
        if predicted is not None:
            return float(predicted)
        
        # Final fallback: use last known value from CSV
        return float(values[-1]) if values else None


def _normalize_switch_state(door_state) -> float:
    try:
        return float(door_state)
    except ValueError:
        if isinstance(door_state, str):
            lowered = door_state.lower()
            if lowered == "open":
                return 1.0
            if lowered == "close":
                return 0.0
    return 0.0

def get_sensor_params(sensor_type):
    params = {
        "PIR": {"min": 0.0, "max": 1.0, "step": 1.0, "state": 0.0, "direction": 0, "consumption": None},
        "Temperature": {"min": 18.0, "max": 50.0, "step": 0.5, "state": 18.0, "direction": None, "consumption": None},
        "Switch": {"min": 0, "max": 1, "step": 1, "state": 0, "direction": None, "consumption": None},
        "Smart Meter": {"min": 0.0, "max": 5000.0, "step": 10.0, "state": 0.0, "direction": None, "consumption": 0.0},
        "Weight": {"min": 0.0, "max": 1.0, "step": 1.0, "state": 0.0, "direction": None, "consumption": None},
    }
    return params.get(
        sensor_type,
        {"min": 0.0, "max": 1.0, "step": 1.0, "state": 0.0, "direction": None, "consumption": None},
    )


def _last_slope_deg_per_min(series) -> float:
    """Return the final slope in C/min (last difference)."""
    if not series:
        return 0.0
    times, vals = series
    if not times or not vals or len(times) < 2:
        return 0.0
    t2, t1 = float(times[-1]), float(times[-2])
    v2, v1 = float(vals[-1]), float(vals[-2])
    dt = (t2 - t1)
    if dt <= 0:
        return 0.0
    return (v2 - v1) / dt


def add_sensor(canvas, event, load_active):
    global add_point_enabled
    if add_point_enabled:
        return

    x = int(canvas.canvasx(event.x))
    y = int(canvas.canvasy(event.y))

    # Build device candidates from both data structures and what is currently drawn.
    device_names = set()
    for dev in devices or []:
        if hasattr(dev, "name") and dev.name:
            device_names.add(str(dev.name).strip())
    for dev in devices_file or []:
        if hasattr(dev, "name") and dev.name:
            device_names.add(str(dev.name).strip())

    try:
        for item_id in canvas.find_withtag("device"):
            tags = canvas.gettags(item_id)
            if tags:
                # First tag is the device name in draw_device().
                name_tag = str(tags[0]).strip()
                if name_tag and name_tag != "device":
                    device_names.add(name_tag)
    except Exception:
        pass

    dialog = SensorDialog(canvas.master, "Add sensor", device_names=sorted(n for n in device_names if n))
    if dialog.result:
        name, type, min_val, max_val, step, state, direction, consumption, associated_device = dialog.result
        sensor = Sensor(
            name=name,
            x=x,
            y=y,
            type=type,
            min_val=float(min_val),
            max_val=float(max_val),
            step=float(step),
            state=float(state),
            direction=direction,
            consumption=consumption,
            associated_device=associated_device,
        )

        # write to the right list according to load_active
        if load_active:
            sensors_file.append(sensor)
        else:
            sensors.append(sensor)

        draw_sensor(canvas, sensor)


def get_last_real_temperature(sensor_name: str, window_minutes: int = 10):
    if not sensor_name:
        return None

    mapping = _load_sensor_map()
    cfg = mapping.get(sensor_name, {})

    df = None

    # 1) if bound to DHT via GPIO, try GPIO
    if isinstance(cfg, dict) and cfg.get("by") == "dht":
        gpio = cfg.get("gpio")
        if gpio is not None:
            try:
                df = load_temp_by_gpio_any_csv(int(gpio))
            except Exception as e:
                print(f"[WARN] load_temp_by_gpio_any_csv failed for {sensor_name}: {e}")

    # 2) otherwise try by label
    if df is None or df.empty:
        try:
            df = load_temp_by_label_any_csv(sensor_name)
        except Exception as e:
            print(f"[WARN] load_temp_by_label_any_csv failed for {sensor_name}: {e}")
            df = None

    if df is None or df.empty or "value" not in df.columns:
        return None

    # keep only valid numeric values
    df_valid = df.dropna(subset=["value"])
    if df_valid.empty:
        return None

    # latest value (i.e., at that minute)
    try:
        latest = float(df_valid["value"].iloc[-1])
        return latest
    except Exception:
        return None
    
      
def infer_room_state(sensor_name: str, window_minutes: int = 20) -> str:
    if not sensor_name:
        return "unknown"

    mapping = _load_sensor_map()
    cfg = mapping.get(sensor_name, {})

    df = None
    if isinstance(cfg, dict) and cfg.get("by") == "dht":
        gpio = cfg.get("gpio")
        if gpio is not None:
            df = load_temp_by_gpio_any_csv(int(gpio))

    if df is None or df.empty:
        df = load_temp_by_label_any_csv(sensor_name)

    if df is None or df.empty or "value" not in df.columns:
        return "unknown"

    tail = df.tail(window_minutes)
    if len(tail) < 2:
        return "unknown"

    t0 = float(tail["value"].iloc[0])
    t1 = float(tail["value"].iloc[-1])
    delta = t1 - t0
    slope = delta / max(1, len(tail) - 1)  # °C for minute

    if slope > 0.15 and t1 >= 26:
        return "cooking"      
    elif slope > 0.05:
        return "heating"     
    elif slope < -0.05:
        return "cooling"    
    else:
        return "stable"


def compute_temperature(sensor, heating_factor, delta_seconds, current_datetime=None, active_devices=None):
    """Compute next Temperature sensor state with no UI side effects."""
    name, x, y = sensor.name, sensor.x, sensor.y
    max_val = float(sensor.max_val)
    current_state = float(sensor.state)

    # 1 real second = 1 simulated minute
    prev_sim_min = float(TEMP_SIM_MIN.get(name, 0.0))
    delta_sim_min = float(delta_seconds or 0.0)

    # Clamp delta to reasonable range (0.1 to 120 minutes per step)
    delta_sim_min = max(0.1, min(120.0, delta_sim_min))

    sim_min = prev_sim_min + delta_sim_min
    TEMP_SIM_MIN[name] = sim_min

    # Keep a recent buffer
    recent = TEMP_RECENT.get(name)
    if recent is None:
        recent = deque(maxlen=30)  # last 30 minutes
        TEMP_RECENT[name] = recent
    recent.append(float(current_state))

    # Preload CSV target (if any) so we can decide whether to apply daily cycle.
    csv_target = get_replay_temperature(name, current_datetime)

    # Base temperature follows daily cycle only if CSV data is available.
    if csv_target is None:
        if name not in TEMP_BASELINE:
            TEMP_BASELINE[name] = float(current_state)
        base_temp = TEMP_BASELINE[name]
    else:
        if current_datetime is not None:
            hour_of_day = current_datetime.hour + current_datetime.minute / 60.0
        else:
            time_of_day = (sim_min % (24 * 60))
            hour_of_day = time_of_day / 60.0

        phase = ((hour_of_day - 4.0) / 24.0) * 2 * math.pi
        base_temp = 17.5 + 2.5 * math.sin(phase)

    # Device heat contribution
    device_heat = 0.0
    heat_by_type = {
        "Oven": 3.5,
        "Computer": 0.6,
        "Washing_Machine": 1.2,
        "Coffee_Machine": 1.0,
        "Dishwasher": 1.2,
        "Fridge": 0.3,
    }
    radius_by_type = {
        "Oven": 220.0,
        "Computer": 140.0,
        "Washing_Machine": 160.0,
        "Coffee_Machine": 120.0,
        "Dishwasher": 160.0,
        "Fridge": 120.0,
    }
    default_heat = 0.8
    default_radius = 140.0

    if active_devices:
        for dev in active_devices:
            if not isinstance(dev, Device):
                continue

            if dev.state == 1:
                dist = ((float(dev.x) - float(x)) ** 2 + (float(dev.y) - float(y)) ** 2) ** 0.5
                max_heat = heat_by_type.get(dev.type, default_heat)
                radius = radius_by_type.get(dev.type, default_radius)
                if dist < radius:
                    device_heat += max_heat * (1.0 - dist / radius)

    # Allow a direct external heating factor contribution (domain input from orchestrator)
    target_temp = base_temp + device_heat + (float(heating_factor) * 0.5)

    if csv_target is not None:
        try:
            csv_target = float(csv_target)
            csv_weight = 0.9
            target_temp = (csv_target * csv_weight) + (target_temp * (1.0 - csv_weight))
        except Exception:
            pass

    time_constant = 30.0
    alpha = 1.0 - math.exp(-delta_sim_min / time_constant)
    new_state = current_state + alpha * (target_temp - current_state)

    effective_min = 0.0
    effective_max = max_val
    if csv_target is not None:
        try:
            effective_min = min(effective_min, float(csv_target))
            effective_max = max(effective_max, float(csv_target))
        except Exception:
            pass
    new_state = max(effective_min, min(effective_max, new_state))
    new_state = round(new_state, 2)

    recent.append(float(new_state))

    if abs(new_state - current_state) > 0.0:
        TEMP_GREEN_UNTIL[name] = sim_min + 5.0

    return new_state

def _device_type_to_appliance_key(dev_type: Optional[str]) -> Optional[str]:
    mapping = {
        "computer": "computer",
        "coffee_machine": "coffee_machine",
        "dishwasher": "dishwasher",
        "refrigerator": "refrigerator",
        "washing_machine": "washing_machine",
    }
    if not dev_type:
        return None
    return mapping.get(str(dev_type).strip().lower())


def _resolve_llm_catalog_path(value: str | Path | None) -> Path:
    if value is None:
        return Path()

    path = Path(value)
    if path.is_absolute():
        return path

    candidate = LLM_PROFILE_CATALOG_PATH.parent / path
    if candidate.exists():
        return candidate

    legacy_candidate = LLM_PROFILE_CATALOG_LEGACY_PATH.parent / path
    if legacy_candidate.exists():
        return legacy_candidate

    return candidate


def _load_llm_profile_catalog() -> dict:
    global LLM_PROFILE_CATALOG_CACHE, LLM_PROFILE_CATALOG_MTIME, LLM_PROFILE_CATALOG_ACTIVE_PATH
    try:
        catalog_path = LLM_PROFILE_CATALOG_PATH
        if not catalog_path.exists() and LLM_PROFILE_CATALOG_LEGACY_PATH.exists():
            catalog_path = LLM_PROFILE_CATALOG_LEGACY_PATH

        if not catalog_path.exists():
            LLM_PROFILE_CATALOG_CACHE = {}
            LLM_PROFILE_CATALOG_MTIME = None
            LLM_PROFILE_CATALOG_ACTIVE_PATH = None
            return {}

        mtime = float(catalog_path.stat().st_mtime)
        if (
            LLM_PROFILE_CATALOG_CACHE is not None
            and LLM_PROFILE_CATALOG_MTIME == mtime
            and LLM_PROFILE_CATALOG_ACTIVE_PATH == catalog_path
        ):
            return LLM_PROFILE_CATALOG_CACHE

        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
        LLM_PROFILE_CATALOG_CACHE = data
        LLM_PROFILE_CATALOG_MTIME = mtime
        LLM_PROFILE_CATALOG_ACTIVE_PATH = catalog_path
        return data
    except Exception:
        return {}


def _load_llm_cycle_curve(profile: dict) -> Optional[tuple[list[float], list[float]]]:
    """Load selected cycle (minutes, power_W) from LLM runtime pkl by cycle_id."""
    try:
        appliance_key = str(profile.get("appliance_key") or "").strip()
        cycle_id = int(profile.get("selected_cycle_id"))
        pkl_path = _resolve_llm_catalog_path(str(profile.get("pkl_path") or "").strip())
    except Exception:
        return None

    cache_key = (appliance_key, cycle_id)
    if cache_key in LLM_CYCLE_CURVE_CACHE:
        return LLM_CYCLE_CURVE_CACHE[cache_key]

    if not pkl_path.is_file():
        LLM_CYCLE_CURVE_CACHE[cache_key] = None
        return None

    try:
        with open(pkl_path, "rb") as fp:
            cycles = pickle.load(fp)
    except Exception:
        LLM_CYCLE_CURVE_CACHE[cache_key] = None
        return None

    if not isinstance(cycles, list):
        LLM_CYCLE_CURVE_CACHE[cache_key] = None
        return None

    selected = None
    for c in cycles:
        try:
            if int(c.get("cycle_id")) == cycle_id:
                selected = c
                break
        except Exception:
            continue

    if not selected:
        LLM_CYCLE_CURVE_CACHE[cache_key] = None
        return None

    df = pd.DataFrame(selected.get("data") or [])
    if df.empty or "time" not in df.columns or "value" not in df.columns:
        LLM_CYCLE_CURVE_CACHE[cache_key] = None
        return None

    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["time", "value"]).sort_values("time").reset_index(drop=True)
    if len(df) < 2:
        LLM_CYCLE_CURVE_CACHE[cache_key] = None
        return None

    t_min = (df["time"] - df["time"].iloc[0]).dt.total_seconds().to_numpy(dtype=float) / 60.0
    vals = df["value"].to_numpy(dtype=float)
    if len(t_min) < 2 or float(t_min[-1]) <= 0.0:
        LLM_CYCLE_CURVE_CACHE[cache_key] = None
        return None

    out = (t_min.tolist(), vals.tolist())
    LLM_CYCLE_CURVE_CACHE[cache_key] = out
    return out


def _interp_cycle_value(minutes_axis: list[float], values_axis: list[float], elapsed_min: float) -> float:
    if not minutes_axis or not values_axis:
        return 0.0
    if len(minutes_axis) == 1:
        return max(0.0, float(values_axis[0]))

    total = float(minutes_axis[-1])
    if total <= 0.0:
        return max(0.0, float(values_axis[-1]))

    x = float(elapsed_min) % total
    i = bisect_right(minutes_axis, x)
    if i <= 0:
        return max(0.0, float(values_axis[0]))
    if i >= len(minutes_axis):
        return max(0.0, float(values_axis[-1]))

    x0 = float(minutes_axis[i - 1])
    x1 = float(minutes_axis[i])
    y0 = float(values_axis[i - 1])
    y1 = float(values_axis[i])
    if x1 <= x0:
        return max(0.0, y0)
    alpha = (x - x0) / (x1 - x0)
    return max(0.0, y0 + alpha * (y1 - y0))


def _get_llm_smartmeter_consumption(
    sensor_name: str,
    dev_type: Optional[str],
    dev_state: int,
    current_datetime: Optional[datetime],
) -> Optional[float]:
    appliance_key = _device_type_to_appliance_key(dev_type)
    if not appliance_key:
        return None

    catalog = _load_llm_profile_catalog()
    profile = catalog.get(appliance_key) if isinstance(catalog, dict) else None
    if not isinstance(profile, dict):
        return None

    if dev_state != 1:
        LLM_SENSOR_ON_START.pop(sensor_name, None)
        return 0.0

    start_dt = LLM_SENSOR_ON_START.get(sensor_name)
    now_dt = current_datetime or datetime.now()
    if start_dt is None:
        LLM_SENSOR_ON_START[sensor_name] = now_dt
        start_dt = now_dt

    curve = _load_llm_cycle_curve(profile)
    if not curve:
        return None

    elapsed_min = max(0.0, (now_dt - start_dt).total_seconds() / 60.0)
    minutes_axis, values_axis = curve
    return _interp_cycle_value(minutes_axis, values_axis, elapsed_min)


def _find_associated_device(dev_list, wanted_name):
    if not dev_list or not wanted_name:
        return None
    return next((d for d in dev_list if d.name == wanted_name), None)


def compute_smartmeter_consumption(sensor, devices, delta_seconds, current_datetime, active_cycles_store=None):
    name = sensor.name
    associated_device = sensor.associated_device

    new_consumption = 0.0

    if associated_device:
        associated_dev = _find_associated_device(devices, associated_device)
        if not associated_dev:
            associated_dev = _find_associated_device(devices_file, associated_device)

        dev_name = None
        dev_type = None
        dev_state = 0
        if associated_dev:
            if isinstance(associated_dev, Device):
                dev_name = associated_dev.name
                dev_type = associated_dev.type
                dev_state = associated_dev.state

        llm_consumption = _get_llm_smartmeter_consumption(name, dev_type, int(dev_state), current_datetime)
        if llm_consumption is not None:
            new_consumption = max(0.0, float(llm_consumption))
        elif dev_name is not None and dev_type is not None:
            # Fallback only if no LLM profile exists for this appliance type.
            if dev_state == 1:
                cycles_ref = active_cycles_store if isinstance(active_cycles_store, dict) else {}
                new_consumption = get_device_consumption(
                    dev_name, dev_type, current_datetime, cycles_ref, dev_state
                )
            else:
                new_consumption = 0.0

    return float(new_consumption)


class SensorDialog(simpledialog.Dialog):
    def __init__(self, parent, title=None, device_names=None):
        self._preloaded_device_names = list(device_names or [])
        super().__init__(parent, title)

    def _device_names_for_association(self) -> list[str]:
        names = set()

        for name in self._preloaded_device_names:
            if name:
                names.add(str(name).strip())

        for dev in devices or []:
            if hasattr(dev, "name") and dev.name:
                names.add(str(dev.name).strip())

        for dev in devices_file or []:
            if hasattr(dev, "name") and dev.name:
                names.add(str(dev.name).strip())

        return sorted(n for n in names if n)

    def body(self, master):
        tk.Label(master, text="Sensor name:").grid(row=0)
        tk.Label(master, text="Sensor type:").grid(row=1)

        self.sensor_name = tk.Entry(master)
        self.sensor_name.grid(row=0, column=1)

        self.sensor_type = ttk.Combobox(
            master,
            values=["PIR", "Temperature", "Switch", "Smart Meter", "Weight"],
            state="readonly",
        )
        self.sensor_type.grid(row=1, column=1)
        self.sensor_type.current(0)

        self.direction_label = tk.Label(master, text="Direction (degrees):")
        self.direction_entry = tk.Entry(master)
        self.direction_entry.insert(0, "0")

        self.associated_device_label = tk.Label(master, text="Associated device:")

        devices_names = self._device_names_for_association()

        self.associated_device_combobox = ttk.Combobox(master, values=devices_names, state="readonly")

        self.sensor_type.bind("<<ComboboxSelected>>", self.on_sensor_type_selected)
        self.on_sensor_type_selected(None)

        return self.sensor_name
    # Show 'direction' for PIR or 'associated device' for Smart Meter only.
    def on_sensor_type_selected(self, event):
        type = self.sensor_type.get()

        if type == "PIR":
            self.direction_label.grid(row=2, column=0)
            self.direction_entry.grid(row=2, column=1)

            self.associated_device_label.grid_remove()
            self.associated_device_combobox.grid_remove()

        elif type == "Smart Meter":
            self.direction_label.grid_remove()
            self.direction_entry.grid_remove()

            devices_names = self._device_names_for_association()
            if devices_names:
                self.associated_device_combobox.configure(values=devices_names, state="readonly")
            else:
                self.associated_device_combobox.configure(values=["No devices available"], state="disabled")

            self.associated_device_label.grid(row=2, column=0)
            self.associated_device_combobox.grid(row=2, column=1)

            if devices_names:
                self.associated_device_combobox.current(0)
            else:
                self.associated_device_combobox.current(0)

        else:
            self.direction_label.grid_remove()
            self.direction_entry.grid_remove()
            self.associated_device_label.grid_remove()
            self.associated_device_combobox.grid_remove()

    # Check for empty/duplicate name; require direction (PIR) or device (Smart Meter).
    def validate(self):
        name = self.sensor_name.get().strip()
        if not name:
            messagebox.showwarning("Input not valid", "Sensor name cannot be empty.")
            return False

        # Check both runtime sensors and file sensors
        for s in sensors:
            if name == s.name:
                messagebox.showwarning("Input not valid", "Sensor name already exists.")
                return False
        
        for s in sensors_file:
            if name == s.name:
                messagebox.showwarning("Input not valid", "Sensor name already exists.")
                return False

        if self.sensor_type.get() == "PIR" and not self.direction_entry.get().strip():
            messagebox.showwarning("Input not valid", "Pir sensor direction cannot be empty.")
            return False

        if self.sensor_type.get() == "Smart Meter" and not self.associated_device_combobox.get():
            messagebox.showwarning(
                "Input not valid",
                "Select a device to associate with the Smart Meter.",
            )
            return False

        if self.sensor_type.get() == "Smart Meter" and self.associated_device_combobox.get() == "No devices available":
            messagebox.showwarning(
                "Input not valid",
                "Add at least one device before adding a Smart Meter.",
            )
            return False

        return True

    def apply(self):
        name = self.sensor_name.get()
        type = self.sensor_type.get()
        params = get_sensor_params(type)

        if type == "PIR":
            direction = float(self.direction_entry.get())
            params["direction"] = direction

        associated_device = None
        if type == "Smart Meter":
            associated_device = self.associated_device_combobox.get()

        if type == "Temperature":
            # If a CSV series exists, use its first value as the initial state.
            series = _load_temp_series_for_sensor(name)
            if series:
                _, vals = series
                if vals:
                    real_temp = float(vals[0])
                    params["state"] = min(params["max"], real_temp)

        self.result = (
            name,
            type,
            params["min"],
            params["max"],
            params["step"],
            params["state"],
            params.get("direction", None),
            params["consumption"],
            associated_device,
        )
