import tkinter as tk
import math
import os
import json
from typing import Optional

from consumption_profiles import consumption_profiles, get_device_consumption

# Cache to avoid re-reading CSVs repeatedly when drawing sensors.
_REAL_TEMP_CACHE: dict[str, bool] = {}


def _is_real_temperature_sensor(sensor_name: str, logs_dir: str = "logs") -> bool:
    """Return True if this Temperature sensor is backed by real DHT logs.

    We detect it by:
      1) Trying label-based CSV lookup (sensor_name).
      2) Falling back to sensor_map.json -> gpio lookup.

    This function is intentionally lightweight and cached.
    """
    if not sensor_name:
        return False
    if sensor_name in _REAL_TEMP_CACHE:
        return _REAL_TEMP_CACHE[sensor_name]

    ok = False
    try:
        from dhtlogger import load_temp_by_label_any_csv, load_temp_by_gpio_any_csv

        # 1) label lookup
        df = None
        try:
            df = load_temp_by_label_any_csv(sensor_name, logs_dir=logs_dir)
        except TypeError:
            # older signature (no logs_dir)
            df = load_temp_by_label_any_csv(sensor_name)
        if df is not None and not df.empty and "value" in df.columns:
            ok = True
        else:
            # 2) gpio lookup via sensor_map.json
            mapping_path = "sensor_map.json"
            if os.path.isfile(mapping_path):
                with open(mapping_path, "r", encoding="utf-8") as f:
                    mapping = json.load(f) if f else {}
                cfg = mapping.get(sensor_name, {}) if isinstance(mapping, dict) else {}
                if isinstance(cfg, dict) and cfg.get("by") == "dht":
                    gpio = cfg.get("gpio")
                    if gpio is not None:
                        try:
                            df2 = load_temp_by_gpio_any_csv(int(gpio), logs_dir=logs_dir)
                        except TypeError:
                            df2 = load_temp_by_gpio_any_csv(int(gpio))
                        if df2 is not None and not df2.empty and "value" in df2.columns:
                            ok = True
    except Exception:
        ok = False

    _REAL_TEMP_CACHE[sensor_name] = ok
    return ok


def _temperature_color(sensor_name: str, changing: bool = False) -> str:
    """Color rules for Temperature sensors.

    - If linked to a real DHT sensor -> cyan (azzurro)
    - Else -> green only while changing, red otherwise
    """
    if _is_real_temperature_sensor(sensor_name):
        return "cyan"
    return "green" if changing else "red"


def draw_sensor(canvas, sensor):
    name, x, y, type, min_val, max_val, step, state, direction, consumption, associated_device = sensor
    # Default coloring:
    #   - Temperature sensors: special rules (cyan if linked to real DHT logs; else green only when changing)
    #   - Other sensors: keep legacy behavior (green if above min)
    if type == "Temperature":
        # At draw time we don't know if it's "changing" yet -> show red (or cyan if real)
        color = _temperature_color(name, changing=False)
    else:
        color = "green" if float(state) > float(min_val) else "red"
    rect_tag = f'{name}_rect_sensor'
    text_tag = f'{name}_text_sensor'
    canvas.create_rectangle(x - 5, y - 5, x + 5, y + 5, fill=color, tags=('sensor', rect_tag))
    canvas.create_text(x+7, y, text=name, fill=color, anchor=tk.SW, tags=('sensor', text_tag))

def calculate_distance(x1, y1, x2, y2):
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)

def draw_fov(canvas, x, y, max_distance, fov_angle, direction):
    direction_rad = math.radians(direction)
    fov_half_angle_rad = math.radians(fov_angle / 2)
    vertex1_x = x + max_distance * math.cos(direction_rad - fov_half_angle_rad)
    vertex1_y = y + max_distance * math.sin(direction_rad - fov_half_angle_rad)
    vertex2_x = x + max_distance * math.cos(direction_rad + fov_half_angle_rad)
    vertex2_y = y + max_distance * math.sin(direction_rad + fov_half_angle_rad)
    canvas.delete('fov')
    canvas.create_polygon(x, y, vertex1_x, vertex1_y, vertex2_x, vertex2_y,
                          fill="", outline="blue", width=2, tags='fov')

def get_nearby_device_states(sensor, devices, walls, doors, max_distance=100):
    x1, y1 = sensor[1], sensor[2]
    nearby_device_states = []
    for device in devices:
        # device structure: (name, x, y, type, power, state)
        name, dx, dy, type, power, state = device
        if calculate_distance(x1, y1, dx, dy) <= max_distance:
            if not is_path_blocked_by_walls(x1, y1, dx, dy, walls, doors):
                nearby_device_states.append(state)
    return nearby_device_states

def is_within_fov(sensor, x, y, max_distance, fov_angle):
    # Assume sensor[8] holds the direction.
    sx, sy, direction = sensor[1], sensor[2], sensor[8]
    dx, dy = x - sx, y - sy
    distance = math.hypot(dx, dy)
    if distance > max_distance:
        return False
    angle = math.degrees(math.atan2(dy, dx)) % 360
    if direction is not None:
        direction %= 360
        relative_angle = (angle - direction) % 360
        if relative_angle > 180:
            relative_angle -= 360
        return abs(relative_angle) <= fov_angle / 2
    return False

def find_closest_sensor_without_intersection(point, sensors, walls_coordinates):
    x1, y1 = point
    sensors_sorted = sorted(sensors, key=lambda s: calculate_distance(x1, y1, s[1], s[2]))
    for sensor in sensors_sorted:
        x2, y2 = sensor[1], sensor[2]
        intersects = False
        for i in range(0, len(walls_coordinates), 4):
            p1, p2, p3, p4 = walls_coordinates[i:i + 4]
            if intersect(x1, y1, x2, y2, p1, p2, p3, p4):
                intersects = True
                break
        if not intersects:
            return sensor
    return None

def find_closest_sensor_within_fov(point, sensors, walls_coordinates, doors, max_distance, fov_angle):
    x, y = point
    visible_sensors = [s for s in sensors if is_within_fov(s, x, y, max_distance, fov_angle)]
    visible_sensors.sort(key=lambda s: calculate_distance(x, y, s[1], s[2]))
    for sensor in visible_sensors:
        sx, sy = sensor[1], sensor[2]
        if not is_path_blocked_by_walls(sx, sy, x, y, walls_coordinates, doors):
            return sensor
    return None

def is_path_blocked_by_walls(x1, y1, x2, y2, walls_coordinates, doors):
    for i in range(0, len(walls_coordinates), 4):
        p1, p2, p3, p4 = walls_coordinates[i:i + 4]
        if intersect(x1, y1, x2, y2, p1, p2, p3, p4):
            return True
    for door in doors:
        # door structure: (x1, y1, x2, y2, state).
        if door[4] == "close":
            px1, py1, px2, py2 = door[0], door[1], door[2], door[3]
            if intersect(x1, y1, x2, y2, px1, py1, px2, py2):
                return True
    return False

def on_segment(x1, y1, x2, y2, x, y):
    return min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2)

def orientation(x1, y1, x2, y2, x3, y3):
    val = (y2 - y1) * (x3 - x2) - (x2 - x1) * (y3 - y2)
    if val == 0:
        return 0
    return 1 if val > 0 else 2

def intersect(x1, y1, x2, y2, x3, y3, x4, y4):
    o1 = orientation(x1, y1, x2, y2, x3, y3)
    o2 = orientation(x1, y1, x2, y2, x4, y4)
    o3 = orientation(x3, y3, x4, y4, x1, y1)
    o4 = orientation(x3, y3, x4, y4, x2, y2)
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and on_segment(x1, y1, x2, y2, x3, y3):
        return True
    if o2 == 0 and on_segment(x1, y1, x2, y2, x4, y4):
        return True
    if o3 == 0 and on_segment(x3, y3, x4, y4, x1, y1):
        return True
    if o4 == 0 and on_segment(x3, y3, x4, y4, x2, y2):
        return True
    return False

def find_switch_sensors_by_doors(doors, sensors):
    results = []
    for door in doors:
        x1, y1, x2, y2, state_p = door
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2
        associated_sensors = []
        for sensor in sensors:
            if sensor[3] == "Switch":
                x, y = sensor[1], sensor[2]
                if calculate_distance(center_x, center_y, x, y) < 50:
                    associated_sensors.append(sensor)
        if associated_sensors:
            results.append((door, associated_sensors, state_p))
    return results

def update_sensor_color(canvas, name, state, min_val):
    # Legacy rule for most sensors.
    color = "green" if float(state) > float(min_val) else "red"
    rect_tag = f'{name}_rect_sensor'
    text_tag = f'{name}_text_sensor'
    canvas.itemconfig(rect_tag, fill=color)
    canvas.itemconfig(text_tag, fill=color)


def update_temperature_sensor_color(canvas, name: str, *, changing: bool) -> None:
    """Explicit Temperature sensor color update.

    - Real DHT-backed sensors -> cyan
    - Simulated temperature sensors -> green only while changing
    """
    color = _temperature_color(name, changing=changing)
    rect_tag = f'{name}_rect_sensor'
    text_tag = f'{name}_text_sensor'
    canvas.itemconfig(rect_tag, fill=color)
    canvas.itemconfig(text_tag, fill=color)

def update_devices_consumption(canvas, devices, delta_seconds, timer_app_instance=None):
    if timer_app_instance is None:
        print("Timer not provided to update_devices_consumption.")
        return

    from common import active_cycles
    from datetime import datetime

    # Rebuild simulated datetime
    simulated_time_str = timer_app_instance.get_simulated_time()
    current_date_str = timer_app_instance.current_date
    current_datetime = datetime.strptime(f"{current_date_str} {simulated_time_str}", "%Y-%m-%d %H:%M")

    for i in range(len(devices)):
        name, dx, dy, type, power, state, min_c, max_c, current_cons, cons_dir = devices[i]

        if state == 1:
            if name in active_cycles:
                start_time, cycle_type = active_cycles[name]
                elapsed_min = (current_datetime - start_time).total_seconds() / 60.0
                profile_duration = max(consumption_profiles[cycle_type]["profile"].keys())

                # At end of profile: for non-continuous devices, turn OFF and close cycle.
                # Continuous: Refrigerator and Computer continue in duration module.
                if elapsed_min > profile_duration:
                    if cycle_type not in ["Fridge", "Computer"]:
                        # Turn off the device and close the cycle
                        devices[i] = (name, dx, dy, type, power, 0, min_c, max_c, 0, 0)
                        try:
                            del active_cycles[name]
                        except KeyError:
                            pass
                        if canvas is not None:
                            canvas.itemconfig(name, fill="red")
                        continue
                    else:
                        elapsed_min = elapsed_min % profile_duration  # ciclo continuo

                # Calculate consumption
                current_consumption = get_device_consumption(
                    name, cycle_type, current_datetime, active_cycles, state
                )
                devices[i] = (name, dx, dy, type, power, state, min_c, max_c, current_consumption, cons_dir)
            else:
                # Device turned on but without active cycle: use profile of its type
                current_consumption = get_device_consumption(
                    name, type, current_datetime, active_cycles, state
                )
                devices[i] = (name, dx, dy, type, power, state, min_c, max_c, current_consumption, cons_dir)
        else:
            # if OFF, consumption is zero
            devices[i] = (name, dx, dy, type, power, state, min_c, max_c, 0, cons_dir)
