import os
import csv
import json
import glob
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk
from datetime import datetime, date
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.ticker import MaxNLocator, FormatStrFormatter
import pandas as pd

from sensor import sensors
from read import read_sensors, read_devices
from device import devices
import dhtlogger
from models import Sensor, Device

plt.rcParams.update({
    "axes.titlesize": 16,
    "axes.labelsize": 14,
    "xtick.labelsize": 10,
    "ytick.labelsize": 12,
    "legend.fontsize": 12
})

_LLM_PROFILE_PATH = "llm_smartmeter_profiles.json"


def _discover_selected_case_csv(appliance_key: str) -> str | None:
    """Find selected_case_latest_incomplete_day.csv for the appliance, tolerant to folder typos."""
    if not appliance_key:
        return None

    # Generic search first: robust to naming variants (e.g. Computer vs Computerr).
    pattern = os.path.join(
        "LLM",
        "smartmeter",
        "*",
        "*",
        "selected_case_latest_incomplete_day.csv",
    )
    candidates = sorted(glob.glob(pattern))

    # Prefer paths that contain the appliance key text.
    key_tokens = {
        "computer": ["computer"],
        "coffee_machine": ["coffee", "machine"],
        "dishwasher": ["dishwasher"],
        "refrigerator": ["refrigerator", "fridge"],
        "washing_machine": ["washing", "machine"],
    }.get(appliance_key, [appliance_key])

    for p in candidates:
        low = p.replace("\\", "/").lower()
        if all(tok in low for tok in key_tokens):
            return p

    # If nothing matches tokens, return first discovered file as last resort.
    return candidates[0] if candidates else None


def _load_llm_profiles(path: str = _LLM_PROFILE_PATH) -> dict:
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _appliance_key_from_device_type(device_type: str | None) -> str | None:
    mapping = {
        "computer": "computer",
        "coffee_machine": "coffee_machine",
        "dishwasher": "dishwasher",
        "refrigerator": "refrigerator",
        "washing_machine": "washing_machine",
    }
    if not device_type:
        return None
    return mapping.get(str(device_type).strip().lower())


def _associated_device_name(sensor_name: str, sensor_states: dict) -> str | None:
    data = sensor_states.get(sensor_name, {}) if isinstance(sensor_states, dict) else {}
    assoc = data.get("associated_device")
    if isinstance(assoc, str) and assoc.strip():
        return assoc.strip()

    for s in sensors:
        if isinstance(s, Sensor):
            if s.name == sensor_name and getattr(s, "associated_device", None):
                return str(s.associated_device).strip()
        else:
            if len(s) > 10 and s[0] == sensor_name and s[10] not in (None, "", "None"):
                return str(s[10]).strip()

    for s in read_sensors:
        if isinstance(s, Sensor):
            if s.name == sensor_name and getattr(s, "associated_device", None):
                return str(s.associated_device).strip()
        else:
            if len(s) > 10 and s[0] == sensor_name and s[10] not in (None, "", "None"):
                return str(s[10]).strip()

    return None


def _device_type_for_name(device_name: str | None) -> str | None:
    if not device_name:
        return None
    for d in devices:
        if isinstance(d, Device):
            if d.name == device_name:
                return d.type
        else:
            if len(d) > 3 and d[0] == device_name:
                return d[3]

    for d in read_devices:
        if isinstance(d, Device):
            if d.name == device_name:
                return d.type
        else:
            if len(d) > 3 and d[0] == device_name:
                return d[3]
    return None


def _llm_info_for_smartmeter(sensor_name: str, sensor_states: dict) -> dict:
    profiles = _load_llm_profiles()
    associated_device = _associated_device_name(sensor_name, sensor_states)
    device_type = _device_type_for_name(associated_device)
    appliance_key = _appliance_key_from_device_type(device_type)
    if not appliance_key:
        return {}
    payload = profiles.get(appliance_key)
    if isinstance(payload, dict):
        out = dict(payload)
        out.setdefault("source", "catalog")
        return out

    # Fallback for old runs where catalog file does not exist yet.
    fallback_path = _discover_selected_case_csv(appliance_key)
    if not os.path.isfile(fallback_path):
        return {}
    try:
        row = pd.read_csv(fallback_path).iloc[0].to_dict()
        dominant_cluster = None
        dominant_cluster_n_cycles = None
        try:
            chosen_k_raw = row.get("chosen_k")
            chosen_k = int(float(chosen_k_raw)) if chosen_k_raw not in (None, "") else None
            base_dir = os.path.dirname(fallback_path)
            if chosen_k is not None:
                summary_path = os.path.join(base_dir, f"results_k{chosen_k}", "cluster_summary.csv")
            else:
                summary_path = os.path.join(base_dir, "cluster_summary.csv")
            if os.path.isfile(summary_path):
                summary_df = pd.read_csv(summary_path)
                if not summary_df.empty and {"cluster", "n_cycles"}.issubset(set(summary_df.columns)):
                    summary_df["cluster"] = pd.to_numeric(summary_df["cluster"], errors="coerce")
                    summary_df["n_cycles"] = pd.to_numeric(summary_df["n_cycles"], errors="coerce")
                    summary_df = summary_df.dropna(subset=["cluster", "n_cycles"])
                    if not summary_df.empty:
                        summary_df = summary_df.sort_values(["n_cycles", "cluster"], ascending=[False, True], kind="mergesort")
                        dominant_cluster = int(summary_df.iloc[0]["cluster"])
                        dominant_cluster_n_cycles = int(summary_df.iloc[0]["n_cycles"])
        except Exception:
            pass

        return {
            "appliance_key": appliance_key,
            "chosen_k": row.get("chosen_k"),
            "dominant_cluster": dominant_cluster,
            "dominant_cluster_n_cycles": dominant_cluster_n_cycles,
            "selected_cluster": row.get("cluster"),
            "selected_cycle_id": row.get("cycle_id"),
            "source": "selected_case_csv",
        }
    except Exception:
        return {}


def _draw_smartmeter_info_box(ax, info: dict):
    if info:
        lines = [
            f"LLM source: {info.get('source', '?')}",
            f"appliance: {info.get('appliance_key', '?')}",
            f"k: {info.get('chosen_k', '?')}",
            f"dominant cluster: {info.get('dominant_cluster', '?')} (n={info.get('dominant_cluster_n_cycles', '?')})",
            f"selected cluster: {info.get('selected_cluster', '?')}",
            f"selected cycle: {info.get('selected_cycle_id', '?')}",
        ]
    else:
        lines = [
            "LLM metadata not found",
            "Run LLM Smart Meter once",
            "or ensure selected_case CSV exists",
        ]
    ax.text(
        0.015,
        0.985,
        "\n".join(lines),
        transform=ax.transAxes,
        va="top",
        ha="left",
        fontsize=9,
        bbox={"boxstyle": "round,pad=0.3", "facecolor": "white", "alpha": 0.8, "edgecolor": "0.7"},
    )

def _parse_datetime(time_str: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(time_str, fmt)
        except ValueError:
            continue
    # if only "HH:MM" arrives I turn it into dummy datetime with date 1900-01-01
    return datetime.strptime(time_str, "%H:%M").replace(year=1900, month=1, day=1)

def _align_len(lst, target_len, fill=None):
    if lst is None:
        return [fill] * target_len
    out = list(lst)
    if len(out) < target_len:
        out.extend([fill] * (target_len - len(out)))
    elif len(out) > target_len:
        out = out[:target_len]
    return out

def _build_dataframe(time_list_str, values_list):
    time_list = [_parse_datetime(t) for t in time_list_str]
    vals = pd.to_numeric(pd.Series(values_list), errors="coerce")
    df = pd.DataFrame({"timestamp": time_list, "value": vals})
    df = df.dropna(subset=["value"])
    if df.empty:
        return df
    df.sort_values("timestamp", inplace=True)
    df = df.drop_duplicates(subset="timestamp", keep="last")
    df.set_index("timestamp", inplace=True)
    # resample per minute and ffill for clean lines
    df = df.resample("1min").ffill()
    return df

def _sensor_type(name: str, sensor_states: dict):
    # from sensor_states
    t = sensor_states.get(name, {}).get("type")
    if t:
        return t
    # from runtime
    for s in sensors:
        if isinstance(s, Sensor):
            if s.name == name:
                return s.type
        else:
            if s[0] == name:
                return s[3]
    # from uploaded from files
    for s in read_sensors:
        if isinstance(s, Sensor):
            if s.name == name:
                return s.type
        else:
            if s[0] == name:
                return s[3]
    # if 'consumption' exists = Smart Meter
    if "consumption" in sensor_states.get(name, {}):
        return "Smart Meter"
    return None


def _latest_interactions_csv():
    logs_root = "logs"
    if not os.path.isdir(logs_root):
        return None
    candidates = []
    for name in os.listdir(logs_root):
        folder = os.path.join(logs_root, name)
        csv_path = os.path.join(folder, "interactions.csv")
        if os.path.isdir(folder) and os.path.isfile(csv_path):
            candidates.append((os.path.getmtime(csv_path), csv_path))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]

def _load_consumption_from_interactions(sensor_name: str) -> dict:
    path = _latest_interactions_csv()
    if not path:
        return {}
    out = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("event_type") == "sensor" and row.get("name") == sensor_name:
                    # 'value' is consumption detected by the sensor
                    ts = row.get("timestamp_sim", "")
                    val = row.get("value", "")
                    try:
                        out[ts] = float(val)
                    except Exception:
                        # if not numeric, skip
                        continue
    except Exception:
        return {}
    return out

def _match_full_or_suffix(times: list[str], full_ts_to_val: dict) -> list:
    values = []
    keys = list(full_ts_to_val.keys())
    for t in times:
        key = None
        if len(t) == 5:  # HH:MM
            # Search for a key that ends with ' HH:MM'
            suffix = f" {t}"
            for k in keys:
                if k.endswith(suffix):
                    key = k
                    break
        else:
            if t in full_ts_to_val:
                key = t
        values.append(full_ts_to_val[key] if key is not None else None)
    return values


def _load_sensor_map(path: str = "sensor_map.json") -> dict:
    """Load sensor_map.json if exists"""
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _get_binding_dht_gpio(sensor_name: str) -> int | None:
    """Get GPIO binding for temperature sensor"""
    mp = _load_sensor_map()
    v = mp.get(sensor_name)
    if isinstance(v, dict) and v.get("by") == "dht":
        try:
            return int(v.get("gpio"))
        except Exception:
            return None
    return None


def _get_binding_ip(sensor_name: str) -> str | None:
    """Get IP binding for smart meter sensor"""
    mp = _load_sensor_map()
    v = mp.get(sensor_name)
    if isinstance(v, dict) and v.get("by") == "ip" and isinstance(v.get("value"), str):
        ip = v["value"].strip()
        return ip if ip else None
    return None


def _simple_rebase_to_day(df: pd.DataFrame, ref_day: date) -> pd.DataFrame:
    """Rebase dataframe index to a reference day"""
    if df is None or df.empty:
        return df
    df = df.copy()
    new_index = [datetime.combine(ref_day, ts.time()) for ts in df.index]
    df.index = new_index
    df = df.sort_index()
    return df


# Graphic design (manual)

def show_graphs(canvas, sensor_states):
    def generate_graph(sensor, sensor_data, frame):
        fig, ax = plt.subplots(figsize=(12, 6))

        time_list = sensor_data.get('time', [])
        state_list = sensor_data.get('state', [])

        sensor_type = _sensor_type(sensor, sensor_states)

        if sensor_type == "Smart Meter":
            consumption_list = sensor_data.get('consumption')
            if not consumption_list:
                m = _load_consumption_from_interactions(sensor)
                if m:
                    consumption_list = _match_full_or_suffix(time_list, m)
            if not consumption_list:
                # no consumption available: blank message and graph
                ax.text(0.5, 0.5, "Consumption not available for this Smart Meter", ha="center", va="center", transform=ax.transAxes)
                y_series = []
            else:
                y_series = _align_len(consumption_list, len(time_list), fill=None)
            y_label = "Power (W)"
        else:
            y_series = state_list
            if sensor_type == "Temperature":
                y_label = "Temperature (°C)"
            elif sensor_type in ("PIR", "Switch"):
                y_label = "State"
            else:
                y_label = "Value"

        df = _build_dataframe(time_list, y_series) if y_series else pd.DataFrame()
        
        # Load real data if binding exists
        df_real = pd.DataFrame()
        if sensor_type == "Temperature":
            gpio = _get_binding_dht_gpio(sensor)
            if gpio is not None:
                df_real = dhtlogger.load_temp_by_gpio_any_csv(gpio, logs_dir="devices")
        elif sensor_type == "Smart Meter":
            # Smart Meter "real" overlay is intentionally disabled.
            df_real = pd.DataFrame()
        
        # Rebase real data to match simulated timeline
        if not df_real.empty and not df.empty:
            df_real = df_real.dropna(subset=["value"])
            if not df_real.empty:
                ref_day = df.index[0].date()
                # Check if real data contains any data from the simulated day
                df_real_for_ref_day = df_real[df_real.index.date == ref_day]
                if not df_real_for_ref_day.empty:
                    # Real data exists for this day, use it
                    df_real = df_real_for_ref_day
                else:
                    # No real data for simulated day, don't show misaligned data
                    df_real = pd.DataFrame()
                
                if not df_real.empty:
                    df_real = _simple_rebase_to_day(df_real, ref_day)
                    # Filter to sim time range
                    sim_min = df.index[0]
                    sim_max = df.index[-1]
                    df_real = df_real[(df_real.index >= sim_min) & (df_real.index <= sim_max)]
        
        if df.empty and df_real.empty:
            if y_series:  # data existed but was not valid
                ax.text(0.5, 0.5, "No valid data to plot", ha="center", va="center", transform=ax.transAxes)
        else:
            # Plot simulated data
            if not df.empty:
                unique_vals = set(df["value"].dropna().unique().tolist())
                is_binary = unique_vals.issubset({0.0, 1.0})
                if is_binary and sensor_type not in ("Smart Meter", "Temperature"):
                    ax.plot(df.index, df["value"], drawstyle='steps-post', marker='o', linestyle='-', label=f"{sensor} (sim)", color='blue')
                    ax.set_ylim(-0.1, 1.1)
                    ax.set_yticks([0, 1])
                else:
                    ax.plot(df.index, df["value"], linestyle='-', linewidth=1.5, marker='o', markersize=2, label=f"{sensor} (sim)", color='blue')
            
            # Plot real data if available
            if not df_real.empty:
                ax.plot(df_real.index, df_real["value"], linestyle='--', linewidth=1.5, label=f"{sensor} (real)", color='orange')

        # Use simulated data for date, fallback to real
        df_for_date = df if not df.empty else df_real
        try:
            date_str = df_for_date.index[0].date() if not df_for_date.empty else ""
        except Exception:
            date_str = ""
        title = f"{sensor} - {date_str}"
        if sensor_type == "Smart Meter":
            info = _llm_info_for_smartmeter(sensor, sensor_states)
            _draw_smartmeter_info_box(ax, info)
        ax.set_title(title)
        ax.set_xlabel("Time")
        ax.set_ylabel(y_label)

        ax.legend()
        ax.grid(True, linestyle=':', alpha=0.7)
        ax.yaxis.set_major_locator(MaxNLocator(8))
        ax.yaxis.set_major_formatter(FormatStrFormatter('%.1f'))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
        fig.tight_layout()

        canvas_plot = FigureCanvasTkAgg(fig, master=frame)
        toolbar = NavigationToolbar2Tk(canvas_plot, frame)
        toolbar.update()
        toolbar.pack(side=tk.TOP, fill=tk.X)
        canvas_plot.draw()
        canvas_plot.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        plt.close(fig)

    def save_selected_logs():
        selected = [s for s, state in select_sensors.items() if state.get()]
        if not selected:
            messagebox.showwarning("Warning", "Select at least one sensor to generate the graph.")
            return

        graph_window = tk.Toplevel()
        graph_window.title("Graphs from sensors")

        container = ttk.Frame(graph_window)
        canvas = tk.Canvas(container)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        container.pack(fill="both", expand=True)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for sensor in selected:
            frame = ttk.Frame(scrollable_frame)
            frame.pack(fill="both", pady=10)
            generate_graph(sensor, sensor_states[sensor], frame)

    selection_window = tk.Toplevel()
    selection_window.title("Select sensors")

    tk.Label(selection_window, text="Select the sensors for which to generate the graph:").pack(pady=10)
    select_sensors = {s: tk.BooleanVar() for s in sensor_states.keys()}

    select_all_var = tk.BooleanVar(value=False)
    def on_toggle_select_all():
        val = bool(select_all_var.get())
        for var in select_sensors.values():
            var.set(val)

    tk.Checkbutton(
        selection_window,
        text="Select all",
        variable=select_all_var,
        command=on_toggle_select_all,
        fg="blue"
    ).pack(anchor="w", pady=(0, 5))

    for sensor, state in select_sensors.items():
        tk.Checkbutton(selection_window, text=sensor, variable=state).pack(anchor="w")

    tk.Button(selection_window, text="Generate Graphs", command=save_selected_logs).pack(pady=10)

# Graphic design (auto)

def show_graphs_auto(sensor_states, selected_keys, target_frame):
    def generate_graph(sensor, sensor_data, frame):
        fig, ax = plt.subplots(figsize=(12, 6))

        time_list = sensor_data.get('time', [])
        state_list = sensor_data.get('state', [])
        sensor_type = _sensor_type(sensor, sensor_states)

        if sensor_type == "Smart Meter":
            consumption_list = sensor_data.get('consumption')
            if not consumption_list:
                m = _load_consumption_from_interactions(sensor)
                if m:
                    consumption_list = _match_full_or_suffix(time_list, m)
            if not consumption_list:
                ax.text(0.5, 0.5, "Consumption not available for this Smart Meter", ha="center", va="center", transform=ax.transAxes)
                y_series = []
            else:
                y_series = _align_len(consumption_list, len(time_list), fill=None)
            y_label = "Power (W)"
        else:
            y_series = state_list
            if sensor_type == "Temperature":
                y_label = "Temperature (°C)"
            elif sensor_type in ("PIR", "Switch"):
                y_label = "State"
            else:
                y_label = "Value"

        df = _build_dataframe(time_list, y_series) if y_series else pd.DataFrame()
        
        # Load real data if binding exists
        df_real = pd.DataFrame()
        if sensor_type == "Temperature":
            gpio = _get_binding_dht_gpio(sensor)
            if gpio is not None:
                df_real = dhtlogger.load_temp_by_gpio_any_csv(gpio, logs_dir="devices")
        elif sensor_type == "Smart Meter":
            # Smart Meter "real" overlay is intentionally disabled.
            df_real = pd.DataFrame()
        
        # Rebase real data to match simulated timeline
        if not df_real.empty and not df.empty:
            df_real = df_real.dropna(subset=["value"])
            if not df_real.empty:
                ref_day = df.index[0].date()
                # Check if real data contains any data from the simulated day
                df_real_for_ref_day = df_real[df_real.index.date == ref_day]
                if not df_real_for_ref_day.empty:
                    # Real data exists for this day, use it
                    df_real = df_real_for_ref_day
                else:
                    # No real data for simulated day, don't show misaligned data
                    df_real = pd.DataFrame()
                
                if not df_real.empty:
                    df_real = _simple_rebase_to_day(df_real, ref_day)
                    # Filter to sim time range
                    sim_min = df.index[0]
                    sim_max = df.index[-1]
                    df_real = df_real[(df_real.index >= sim_min) & (df_real.index <= sim_max)]
        
        if df.empty and df_real.empty:
            if y_series:
                ax.text(0.5, 0.5, "No valid data to plot", ha="center", va="center", transform=ax.transAxes)
        else:
            # Plot simulated data
            if not df.empty:
                unique_vals = set(df["value"].dropna().unique().tolist())
                is_binary = unique_vals.issubset({0.0, 1.0})
                if is_binary and sensor_type not in ("Smart Meter", "Temperature"):
                    ax.plot(df.index, df["value"], drawstyle='steps-post', marker='o', linestyle='-', label=f"{sensor} (sim)", color='blue')
                    ax.set_ylim(-0.1, 1.1)
                    ax.set_yticks([0, 1])
                else:
                    ax.plot(df.index, df["value"], linestyle='-', linewidth=1.5, marker='o', markersize=2, label=f"{sensor} (sim)", color='blue')
            
            # Plot real data if available
            if not df_real.empty:
                ax.plot(df_real.index, df_real["value"], linestyle='--', linewidth=1.5, label=f"{sensor} (real)", color='orange')

        title = f"Sensor trend: {sensor}"
        if sensor_type == "Smart Meter":
            info = _llm_info_for_smartmeter(sensor, sensor_states)
            _draw_smartmeter_info_box(ax, info)
        ax.set_title(title)
        ax.set_xlabel("Time")
        ax.set_ylabel(y_label)

        ax.legend()
        ax.grid(True, linestyle=':', alpha=0.7)
        ax.yaxis.set_major_locator(MaxNLocator(8))
        ax.yaxis.set_major_formatter(FormatStrFormatter('%.1f'))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
        fig.tight_layout()

        canvas_plot = FigureCanvasTkAgg(fig, master=frame)
        toolbar = NavigationToolbar2Tk(canvas_plot, frame)
        toolbar.update()
        toolbar.pack(side=tk.TOP, fill=tk.X)
        canvas_plot.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        canvas_plot.draw()
        plt.close(fig)

    # clean target_frame
    for w in target_frame.winfo_children():
        w.destroy()

    container = ttk.Frame(target_frame)
    container.pack(fill="both", expand=True)

    for key in selected_keys:
        if key not in sensor_states:
            continue
        card = ttk.Frame(container)
        card.pack(fill="x", pady=10)
        generate_graph(key, sensor_states[key], card)