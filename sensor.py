import tkinter as tk
from tkinter import simpledialog, messagebox
from tkinter import ttk
from utils import draw_sensor, calculate_distance, update_sensor_color, update_temperature_sensor_color
from common import sensor_states
from device import devices
from datetime import datetime
from common import active_cycles
from consumption_profiles import get_device_consumption, consumption_profiles
from read import read_sensors as sensors_file
from read import read_devices as devices_file
import os, json
import math
from dhtlogger import load_temp_by_label_any_csv, load_temp_by_gpio_any_csv
import pandas as pd
from collections import deque
from typing import Optional
from models import Sensor, Device

TEMP_RECENT: dict[str, deque] = {}
TEMP_GREEN_UNTIL: dict[str, float] = {}
TEMP_BASELINE: dict[str, float] = {}

sensors = []
add_point_enabled = False

SENSOR_MAP_PATH = "sensor_map.json"

# cache: per sensor -> (datetimes_list, values_list_C)
TEMP_SERIES: dict[str, Optional[tuple[list[datetime], list[float]]]] = {}
# simulated time (in delta_seconds units) per sensor
TEMP_SIM_MIN: dict[str, float] = {}

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

    dialog = SensorDialog(canvas.master, "Add sensor")
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
            sensors_file.append(sensor.tuple())
        else:
            sensors.append(sensor)

        draw_sensor(canvas, sensor)


def changePIR(canvas, sensor, sensors, new_state=None):
    # Handle both Sensor objects and tuples
    if isinstance(sensor, Sensor):
        name, x, y, type_s, min_val = sensor.name, sensor.x, sensor.y, sensor.type, sensor.min_val
        state = float(sensor.state)
        direction, consumption, associated_device = sensor.direction, sensor.consumption, sensor.associated_device
    else:
        if len(sensor) != 11:
            print(f"Error: wrong sensor structure {sensor}")
            return None, None, sensors
        name, x, y, type_s, min_val, max_val, step, state, direction, consumption, associated_device = sensor
        state = float(state)

    if new_state is None:
        new_state = 1 if state == 0 else 0

    updated_sensors = []
    for s in sensors:
        if s == sensor or (isinstance(s, Sensor) and isinstance(sensor, Sensor) and s.name == sensor.name):
            if isinstance(s, Sensor):
                s.state = float(new_state)
                updated_sensors.append(s)
            else:
                updated_sensors.append(
                    (
                        name, x, y, type_s, min_val, max_val if len(s) > 5 else min_val,
                        step if len(s) > 6 else 1.0,
                        new_state,
                        direction, consumption, associated_device,
                    )
                )
        elif (isinstance(s, Sensor) and s.type == "PIR") or (isinstance(s, tuple) and s[3] == "PIR"):
            if isinstance(s, Sensor):
                s.state = 0.0
                updated_sensors.append(s)
                update_sensor_color(canvas, s.name, 0, s.min_val)
            else:
                updated_sensors.append(
                    (s[0], s[1], s[2], s[3], s[4], s[5], s[6], 0, s[8], s[9], s[10])
                )
                update_sensor_color(canvas, s[0], 0, s[4])
        else:
            updated_sensors.append(s)

    update_sensor_color(canvas, name, new_state, float(min_val))
    return name, new_state, updated_sensors


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

def changeTemperature(canvas, sensor, sensors, heating_factor, delta_seconds, current_datetime=None, active_devices=None):
    """
    Update a Temperature sensor state with dynamic simulation.

    Behavior:
      - Uses a realistic thermal model:
          * Daily cycle (cooler at night, warmer during day)
          * Device heat contribution (computers, appliances generate heat)
          * Thermal inertia (slow, gradual changes)
      - If real CSV exists beyond sim_min: optionally blend/compare
    """
    if len(sensor) != 11:
        print(f"Error: unexpected Temperature structure {sensor}")
        return None, None, sensors

    (
        name, x, y, s_type,
        min_val, max_val, step,
        state, direction, consumption,
        associated_device,
    ) = sensor

    min_val = float(min_val)
    max_val = float(max_val)
    step = float(step)
    current_state = float(state)

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
    # Pass the actual current_datetime for real-time alignment
    csv_target = get_replay_temperature(name, current_datetime)

    # --- DYNAMIC THERMAL MODEL ---
    # Base temperature follows daily cycle only if CSV data is available.
    if csv_target is None:
        if name not in TEMP_BASELINE:
            TEMP_BASELINE[name] = float(current_state)
        base_temp = TEMP_BASELINE[name]
    else:
        # Use real time of day from current_datetime for accurate daily cycle
        if current_datetime is not None:
            hour_of_day = current_datetime.hour + current_datetime.minute / 60.0
        else:
            # Fallback to relative minutes if no current_datetime
            time_of_day = (sim_min % (24 * 60))  # minutes since midnight
            hour_of_day = time_of_day / 60.0

        # Base temperature: 15°C at night (4 AM), 20°C during day (2 PM)
        # Sinusoidal cycle with min at 4:00 and max at 14:00
        phase = ((hour_of_day - 4.0) / 24.0) * 2 * math.pi  # 4 AM = minimum
        base_temp = 17.5 + 2.5 * math.sin(phase)  # oscillates 15-20°C
    
    # Device heat contribution (check nearby active devices)
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
            if len(dev) >= 10:
                dev_name, dev_x, dev_y, dev_type, _, dev_state = dev[:6]
                if dev_state == 1:  # device is ON
                    # Calculate distance to this temperature sensor
                    dist = ((float(dev_x) - float(x))**2 + (float(dev_y) - float(y))**2)**0.5
                    # Heat contribution decays with distance by device type.
                    max_heat = heat_by_type.get(dev_type, default_heat)
                    radius = radius_by_type.get(dev_type, default_radius)
                    if dist < radius:
                        contribution = max_heat * (1.0 - dist / radius)
                        device_heat += contribution
    
    # Target temperature = base + device heat
    target_temp = base_temp + device_heat

    # If a CSV series exists, nudge the target toward historical/replay values.
    # With high weight (0.9), the simulation follows the CSV closely while maintaining some model stability.
    if csv_target is not None:
        try:
            csv_target = float(csv_target)
            csv_weight = 0.9  # High weight: mostly follow CSV (0.9) with ~10% model correction
            target_temp = (csv_target * csv_weight) + (target_temp * (1.0 - csv_weight))
        except Exception:
            pass
    
    # Apply thermal inertia (slow convergence to target)
    # Larger time constant = slower changes (realistic for building thermal mass)
    time_constant = 30.0  # minutes to reach ~63% of target
    alpha = 1.0 - math.exp(-delta_sim_min / time_constant)
    new_state = current_state + alpha * (target_temp - current_state)
    
    # Clamp to reasonable bounds (expand to CSV target if needed)
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


    # Update buffer with the new value
    recent.append(float(new_state))

    updated = []
    for s in sensors:
        if s == sensor:
            updated.append(
                (name, x, y, s_type, min_val, max_val, step, new_state, direction, consumption, associated_device)
            )
        else:
            updated.append(s)

    temp_change_abs = abs(new_state - current_state)
    if temp_change_abs > 0.0:
        TEMP_GREEN_UNTIL[name] = sim_min + 5.0
    green_until = TEMP_GREEN_UNTIL.get(name, 0.0)
    changing = sim_min <= green_until
    if canvas is not None:
        update_temperature_sensor_color(canvas, name, changing=changing)
    return name, new_state, updated


def _get_intraday_power_pattern(associated_device: str, time_of_day_minutes: float, window_days: int = 7) -> Optional[float]:
    """
    Use smart meter history to find the intraday consumption pattern.

    Looks across previous days for typical consumption at this time of day,
    and returns a weighted average (more recent = higher weight).

    Args:
        associated_device: associated device name (pc, wm, dw, etc.)
        time_of_day_minutes: minutes since midnight (0-1440)
        window_days: how many previous days to analyze

    Returns:
        predicted power in W or None if no data is available
    """
    try:
        import smartmeter
    except ImportError:
        return None
    
    # Derive device_id from name
    device_id = smartmeter.derive_device_id(associated_device)
    
    # Load historical data
    df = None
    try:
        df = smartmeter.load_power_by_device_id_any_csv(device_id, logs_dir="logs")
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


def get_replay_smart_meter_consumption(associated_device: str, current_datetime: Optional[datetime] = None) -> Optional[float]:
    """
    Smart meter consumption replay based on historical data, aligned to real time.

    Logic:
    - If current_datetime matches a time in the CSV -> direct lookup (with interpolation)
    - If current_datetime is outside the CSV range:
      - Before: use first value
      - After: use intraday pattern prediction
    
    Args:
        associated_device: device name (PC, WASHER, etc.)
        current_datetime: current datetime to look up
    
    Returns:
        Power consumption in W or None if no data is available
    """
    # Handle None or missing datetime -> fallback to model-only
    if current_datetime is None:
        return None
    
    try:
        import smartmeter
    except ImportError:
        return None
    
    device_id = smartmeter.derive_device_id(associated_device)
    
    try:
        df = smartmeter.load_power_by_device_id_any_csv(device_id, logs_dir="logs")
    except Exception:
        return None
    
    if df is None or df.empty or "value" not in df.columns:
        return None
    
    df_sorted = df.sort_index()
    if df_sorted.empty:
        return None
    
    # Get datetimes from index
    datetimes = df_sorted.index.to_list()
    values = df_sorted["value"].astype(float).to_list()
    
    # Convert to timezone-naive for comparison
    current_dt = current_datetime
    if current_dt.tzinfo is not None:
        current_dt = current_dt.replace(tzinfo=None)
    
    # Before first: use first value
    if current_dt <= datetimes[0]:
        return float(values[0])
    
    # After last: use intraday pattern prediction
    if current_dt >= datetimes[-1]:
        time_of_day = (current_dt.hour * 60 + current_dt.minute)
        predicted = _get_intraday_power_pattern(associated_device, time_of_day)
        if predicted is not None:
            return float(predicted)
        
        # Fallback: use last known value
        return float(values[-1])
    
    # Within available range: interpolate
    for i in range(len(datetimes) - 1):
        dt1, dt2 = datetimes[i], datetimes[i + 1]
        if dt1 <= current_dt <= dt2:
            v1, v2 = values[i], values[i + 1]
            
            # Handle zero duration (same timestamp)
            if dt2 == dt1:
                return float(v1)
            
            # Linear interpolation
            time_diff = (dt2 - dt1).total_seconds()
            time_into_interval = (current_dt - dt1).total_seconds()
            alpha = time_into_interval / time_diff
            return float(v1 + alpha * (v2 - v1))
    
    # Should not reach here if logic is sound, but return last value as fallback
    return float(values[-1])


def changeSmartMeter(canvas, sensor, sensors, devices, delta_seconds, current_datetime):
    if len(sensor) < 11:
        print(f"[WARN] Unexpected Smart Meter structure: {sensor}")
        return sensor[0] if sensor else None, 0.0, sensors

    (
        name,
        x,
        y,
        type,
        min_val,
        max_val,
        step,
        state,
        direction,
        _old_consumption,
        associated_device,
    ) = sensor

    new_consumption = 0.0

    if associated_device:
        # 1) Try historical prediction first
        if current_datetime:
            # Try historical prediction with real datetime alignment
            replay_consumption = get_replay_smart_meter_consumption(associated_device, current_datetime)
            if replay_consumption is not None:
                new_consumption = max(0.0, float(replay_consumption))
            else:
                # 2) Fallback: compute from associated device and active cycles
                associated_dev = next((d for d in devices if d[0] == associated_device), None)
                if not associated_dev and devices:
                    associated_dev = next((d for d in devices if d[0] == associated_device), None)
                if not associated_dev and devices_file:
                    associated_dev = next((d for d in devices_file if d[0] == associated_device), None)

                if associated_dev:
                    dev_name, _, _, dev_type, _, dev_state, *_ = associated_dev
                    if dev_state == 1:
                        new_consumption = get_device_consumption(
                            dev_name, dev_type, current_datetime, active_cycles, dev_state
                        )
                    else:
                        new_consumption = 0.0
        else:
            # If current_datetime is missing, use the legacy method
            associated_dev = next((d for d in devices if d[0] == associated_device), None)
            if not associated_dev and devices:
                associated_dev = next((d for d in devices if d[0] == associated_device), None)
            if not associated_dev and devices_file:
                associated_dev = next((d for d in devices_file if d[0] == associated_device), None)

            if associated_dev:
                dev_name, _, _, dev_type, _, dev_state, *_ = associated_dev
                if dev_state == 1:
                    new_consumption = get_device_consumption(
                        dev_name, dev_type, current_datetime, active_cycles, dev_state
                    )
                else:
                    new_consumption = 0.0

    # update the sensor array with new consumption
    updated = []
    for s in sensors:
        if s == sensor:
            updated.append(
                (
                    name,
                    x,
                    y,
                    type,
                    min_val,
                    max_val,
                    step,
                    state,
                    direction,
                    new_consumption,
                    associated_device,
                )
            )
        else:
            updated.append(s)

    # update color (green if above minimum threshold)
    update_sensor_color(canvas, name, new_consumption, min_val)

    return name, new_consumption, updated


def ChangeWeight(canvas, sensor, sensors, new_state):
    if len(sensor) != 11:
        print(f"Error: unexpected Weight structure {sensor}")
        return None, None, sensors

    (
        name,
        x,
        y,
        type,
        min_val,
        max_val,
        step,
        state,
        direction,
        consumption,
        associated_device,
    ) = sensor

    updated_sensors = []
    for s in sensors:
        if s == sensor:
            updated_sensor = (
                name,
                x,
                y,
                type,
                min_val,
                max_val,
                step,
                new_state,
                direction,
                consumption,
                associated_device,
            )
            updated_sensors.append(updated_sensor)
        else:
            updated_sensors.append(s)

    update_sensor_color(canvas, name, new_state, float(min_val))
    return name, new_state, updated_sensors


class SensorDialog(simpledialog.Dialog):
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

        # Handle both Device objects and tuples
        devices_names_runtime = []
        for d in devices:
            if isinstance(d, Device):
                devices_names_runtime.append(d.name)
            else:
                devices_names_runtime.append(d[0])
        
        devices_names_file = [d[0] for d in devices_file] if devices_file else []
        devices_names = sorted(set(devices_names_runtime + devices_names_file))

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

            self.associated_device_label.grid(row=2, column=0)
            self.associated_device_combobox.grid(row=2, column=1)

            if self.associated_device_combobox["values"]:
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
            if isinstance(s, Sensor):
                if name == s.name:
                    messagebox.showwarning("Input not valid", "Sensor name already exists.")
                    return False
            else:
                if name == s[0]:
                    messagebox.showwarning("Input not valid", "Sensor name already exists.")
                    return False
        
        for s in sensors_file:
            if isinstance(s, tuple) and name == s[0]:
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