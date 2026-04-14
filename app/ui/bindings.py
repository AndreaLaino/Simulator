from __future__ import annotations

import os, re, json, tkinter as tk
from tkinter import messagebox
from typing import Dict, Any

from sensor import sensors
from read import read_sensors, read_devices
from app.logging_setup import setup_logging

logger = setup_logging("ui.bindings")

# ---------- shared helpers ----------

def _sensor_type(name: str, sensor_states: dict) -> str | None:
    t = (sensor_states.get(name) or {}).get("type")
    if t:
        return t
    try:
        for s in (sensors or []):
            if s.name == name:
                return s.type
    except Exception:
        pass
    try:
        for s in (read_sensors or []):
            if s.name == name:
                return s.type
    except Exception:
        pass
    return None

def _is_smart_meter_sensor(name: str, sensor_states: dict) -> bool:
    return _sensor_type(name, sensor_states) == "Smart Meter"

def _all_sensor_names(sensor_states: dict) -> list[str]:
    names = set()
    try:
        names.update((sensor_states or {}).keys())
    except Exception:
        pass
    try:
        for s in (sensors or []):
            names.add(s.name)
    except Exception:
        pass
    try:
        for s in (read_sensors or []):
            names.add(s.name)
    except Exception:
        pass
    return sorted(names)

def _normalize_device_label(value: str) -> str:
    txt = (value or "").replace("_", " ").strip().lower()
    return re.sub(r"\s+", " ", txt)

def _smart_meter_display_name(sensor_name: str, sensor_states: dict) -> str:
    """Return a human-readable device label for Smart Meter logs."""
    data = (sensor_states or {}).get(sensor_name) or {}
    assoc = (data.get("associated_device") or "").strip()

    if not assoc:
        try:
            for s in (sensors or []):
                if s.name == sensor_name and getattr(s, "associated_device", None) not in (None, "", "None"):
                    assoc = str(s.associated_device).strip()
                    break
        except Exception:
            pass

    if not assoc:
        try:
            for s in (read_sensors or []):
                if s.name == sensor_name and getattr(s, "associated_device", None) not in (None, "", "None"):
                    assoc = str(s.associated_device).strip()
                    break
        except Exception:
            pass

    if assoc:
        try:
            for d in (read_devices or []):
                if d.name == assoc:
                    return _normalize_device_label(str(d.type))
        except Exception:
            pass

        try:
            from device import devices as runtime_devices
            for d in (runtime_devices or []):
                if hasattr(d, "name") and hasattr(d, "type") and d.name == assoc:
                    return _normalize_device_label(str(d.type))
        except Exception:
            pass

        return _normalize_device_label(assoc)

    # Heuristic fallback: smart meter ids usually follow "sm_<device_name>".
    # Example: sm_pc -> device "pc" -> type "Computer".
    compact = (sensor_name or "").strip()
    if compact.startswith("sm_") and len(compact) > 3:
        guessed_dev_name = compact[3:]

        try:
            for d in (read_devices or []):
                if d.name == guessed_dev_name:
                    return _normalize_device_label(str(d.type))
        except Exception:
            pass

        try:
            from device import devices as runtime_devices
            for d in (runtime_devices or []):
                if hasattr(d, "name") and hasattr(d, "type") and d.name == guessed_dev_name:
                    return _normalize_device_label(str(d.type))
        except Exception:
            pass

    return _normalize_device_label(sensor_name)

def _load_sensor_map_json(path="sensor_map.json") -> Dict[str, Any]:
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
    except Exception:
        logger.exception("Failed to load sensor_map.json")
    return {}

def _save_sensor_map_json(mapping: dict, path="sensor_map.json"):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
        messagebox.showinfo("Saved", f"Mapping saved in {path}")
    except Exception as e:
        logger.exception("Failed to save sensor_map.json")
        messagebox.showerror("Error", f"Unable to save {path}:\n{e}")


def autostart_bound_ip_loggers(sensor_states: dict):
    """Start Smart Meter loggers from persisted IP mappings in sensor_map.json."""
    mapping = _load_sensor_map_json()
    if not mapping:
        return

    sm_sensor_names = {
        n for n in _all_sensor_names(sensor_states)
        if _is_smart_meter_sensor(n, sensor_states)
    }

    try:
        from smartmeter import start_logger, csv_path_for_device
    except Exception as e:
        logger.warning("Cannot import smartmeter logger APIs: %s", e)
        return

    started = 0
    for sensor_name, cfg in mapping.items():
        if sm_sensor_names and sensor_name not in sm_sensor_names:
            # Ignore stale entries not present in the currently loaded scenario.
            continue

        if not isinstance(cfg, dict) or cfg.get("by") != "ip":
            continue

        ip = (cfg.get("value") or "").strip()
        if not ip:
            continue

        try:
            display_name = _smart_meter_display_name(sensor_name, sensor_states)
            start_logger(
                device_name=display_name,
                ip=ip,
                interval=60,
                device_id=sensor_name,
                csv_path=csv_path_for_device(sensor_name),
            )
            started += 1
        except Exception as e:
            logger.warning("Cannot auto-start smartmeter logger for %s (%s): %s", sensor_name, ip, e)

    if started:
        logger.info("[SmartMeter] auto-started %d logger(s) from sensor_map.json", started)

# ---------- Smart Meter (IP) ----------

def open_bind_ip_ui(root_win: tk.Tk, sensor_states: dict):
    """
    Associate ONLY 'Smart Meter' sensors to a real IP.
    Persist as: { "sensor_name": {"by":"ip","value":"10.195.1.18"} }
    Optionally auto-start the logger.
    """
    win = tk.Toplevel(root_win)
    win.title("Bind Smart Meter to real IP")
    win.geometry("640x520")

    all_names = _all_sensor_names(sensor_states)
    sm_names = [n for n in all_names if _is_smart_meter_sensor(n, sensor_states)]
    current = _load_sensor_map_json()

    container = tk.Frame(win); container.pack(fill="both", expand=True, padx=10, pady=10)
    canv = tk.Canvas(container); canv.pack(side="left", fill="both", expand=True)
    vsb = tk.Scrollbar(container, orient="vertical", command=canv.yview); vsb.pack(side="right", fill="y")
    canv.configure(yscrollcommand=vsb.set)
    inner = tk.Frame(canv); canv.create_window((0,0), window=inner, anchor="nw")

    header = tk.Frame(inner); header.pack(fill="x", pady=(0,6))
    tk.Label(header, text="Sensor (Smart Meter)", width=28, anchor="w", font=("Helvetica", 10, "bold")).grid(row=0, column=0, sticky="w")
    tk.Label(header, text="Real IP", width=24, anchor="w", font=("Helvetica", 10, "bold")).grid(row=0, column=1, sticky="w")

    rows = []
    for name in sm_names:
        row = tk.Frame(inner); row.pack(fill="x", pady=4)
        tk.Label(row, text=name, width=28, anchor="w").grid(row=0, column=0, sticky="w")

        init_ip = ""
        v = current.get(name)
        if isinstance(v, dict) and v.get("by") == "ip":
            init_ip = v.get("value") or ""

        var_ip = tk.StringVar(value=init_ip)
        tk.Entry(row, textvariable=var_ip, width=24).grid(row=0, column=1, sticky="w", padx=(10,0))
        rows.append((name, var_ip))

    inner.bind("<Configure>", lambda e: canv.configure(scrollregion=canv.bbox("all")))

    btns = tk.Frame(win); btns.pack(fill="x", padx=10, pady=(6,10))
    auto_start_var = tk.BooleanVar(value=True)
    tk.Checkbutton(btns, text="Auto-start logger for associated IPs", variable=auto_start_var).pack(side="left")

    def _save_and_close():
        new_map = _load_sensor_map_json()
        changed = []  

        for name, var_ip in rows:
            ip = var_ip.get().strip()
            if ip:
                new_map[name] = {"by": "ip", "value": ip}
                changed.append((name, ip))  
            else:
                if name in new_map:
                    del new_map[name]

        _save_sensor_map_json(new_map)

        if auto_start_var.get():
            from smartmeter import start_logger, csv_path_for_device
            for sensor_name, ip in changed:
                try:
                    display_name = _smart_meter_display_name(sensor_name, sensor_states)
                    if display_name == _normalize_device_label(sensor_name):
                        logger.warning(
                            "Smart Meter %s has no associated device; using sensor name as device label.",
                            sensor_name,
                        )
                    start_logger(
                        device_name=display_name,
                        ip=ip,
                        interval=60,
                        device_id=sensor_name,
                        csv_path=csv_path_for_device(sensor_name),
                    )
                except Exception as e:
                    logger.warning(
                        "Cannot start smartmeter logger for %s (%s): %s",
                        sensor_name, ip, e
                    )

        win.destroy()

    tk.Button(btns, text="Save", command=_save_and_close).pack(side="right")
    tk.Button(btns, text="Close", command=win.destroy).pack(side="right", padx=8)

# ---------- DHT22 (GPIO) ----------

def open_bind_dht_ui(root_win: tk.Tk, sensor_states: dict):
    """
    Associate 'Temperature' sensors to a local DHT on GPIO (BCM numbering).
    Persist as: {"sensor_name":{"by":"dht","gpio":4}} and optionally auto-start logger.
    """
    def _is_temp(name: str) -> bool:
        return _sensor_type(name, sensor_states) == "Temperature"

    names = [n for n in _all_sensor_names(sensor_states) if _is_temp(n)]
    current = _load_sensor_map_json()

    win = tk.Toplevel(root_win)
    win.title("Bind DHT22 (GPIO → sensor)")
    win.geometry("520x420")

    frm = tk.Frame(win); frm.pack(fill="both", expand=True, padx=10, pady=10)
    tk.Label(frm, text="Sensor (Temperature)", width=30, anchor="w").grid(row=0, column=0, sticky="w")
    tk.Label(frm, text="GPIO (BCM)", width=12, anchor="w").grid(row=0, column=1, sticky="w")

    rows = []
    for i, name in enumerate(names, start=1):
        tk.Label(frm, text=name, width=30, anchor="w").grid(row=i, column=0, sticky="w")
        init_gpio = ""
        v = current.get(name)
        if isinstance(v, dict) and v.get("by") == "dht":
            init_gpio = str(v.get("gpio") or "")
        var = tk.StringVar(value=init_gpio)
        tk.Entry(frm, textvariable=var, width=12).grid(row=i, column=1, sticky="w")
        rows.append((name, var))

    bottom = tk.Frame(win); bottom.pack(fill="x", padx=10, pady=(6,10))
    autostart = tk.BooleanVar(value=True)
    tk.Checkbutton(bottom, text="Auto-start DHT logger for associated GPIO", variable=autostart).pack(side="left")

    def _save():
        m = _load_sensor_map_json()
        started = []
        for name, var in rows:
            txt = var.get().strip()
            if not txt:
                if name in m and (m.get(name) or {}).get("by") == "dht":
                    del m[name]
                continue
            try:
                gpio = int(txt)
            except Exception:
                messagebox.showerror("Invalid GPIO", f"{name}: '{txt}' is not a number.")
                return
            m[name] = {"by": "dht", "gpio": gpio}
            if autostart.get():
                try:
                    from dhtlogger import start_dht_logger
                    start_dht_logger(sensor_label=name, gpio=gpio, interval=60)
                    started.append((name, gpio))
                except Exception as e:
                    logger.warning("Cannot start DHT logger on GPIO %s: %s", gpio, e)
        _save_sensor_map_json(m)
        if started:
            logger.info("[DHT] loggers started: %s", started)
        win.destroy()

    tk.Button(bottom, text="Save", command=_save).pack(side="right")
    tk.Button(bottom, text="Close", command=win.destroy).pack(side="right", padx=8)