# graph.py
from __future__ import annotations

import os
import csv
import json
import tkinter as tk
from tkinter import messagebox, ttk
from datetime import datetime, date, timedelta

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.ticker import MaxNLocator, FormatStrFormatter

import pandas as pd

from sensor import sensors
from read import read_sensors
import smartmeter
import dhtlogger


# Matplotlib defaults 
plt.rcParams.update(
    {
        "axes.titlesize": 16,
        "axes.labelsize": 14,
        "xtick.labelsize": 10,
        "ytick.labelsize": 12,
        "legend.fontsize": 12,
    }
)


# Sensor map helpers 
def _load_sensor_map(path: str = "sensor_map.json") -> dict:
    """Load sensor_map.json (if present). Returns {} on error."""
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _get_binding_dht_gpio_for_sensor(sensor_name: str) -> int | None:
    """
    If sensor_map.json contains:
        { "<sensor_name>": {"by":"dht", "gpio": <bcm_int>} }
    return the bcm gpio as int, otherwise None.
    """
    mp = _load_sensor_map()
    v = mp.get(sensor_name)
    if isinstance(v, dict) and v.get("by") == "dht":
        try:
            return int(v.get("gpio"))
        except Exception:
            return None
    return None


def _get_binding_ip_for_sensor(sensor_name: str) -> str | None:
    """
    If sensor_map.json contains:
        { "<sensor_name>": {"by":"ip", "value": "<ip_string>"} }
    return the IP string, otherwise None.
    """
    mp = _load_sensor_map()
    v = mp.get(sensor_name)
    if isinstance(v, dict) and v.get("by") == "ip" and isinstance(v.get("value"), str):
        ip = v["value"].strip()
        return ip if ip else None
    return None


# ----------------- Base utilities -----------------
def _parse_datetime(time_str: str) -> datetime:
    """Parse timestamps coming from your simulator / logs."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(time_str, fmt)
        except ValueError:
            continue
    # Fallback: HH:MM
    return datetime.strptime(time_str, "%H:%M").replace(year=1900, month=1, day=1)


def _align_len(lst, target_len, fill=None):
    """Pad/truncate a list to target_len."""
    if lst is None:
        return [fill] * target_len
    out = list(lst)
    if len(out) < target_len:
        out.extend([fill] * (target_len - len(out)))
    elif len(out) > target_len:
        out = out[:target_len]
    return out


def _build_dataframe(time_list_str, values_list) -> pd.DataFrame:
    """
    Build a 1-minute sampled DataFrame with a DatetimeIndex.
    Values are coerced to numeric, invalid rows dropped.
    """
    time_list = [_parse_datetime(t) for t in time_list_str]
    vals = pd.to_numeric(pd.Series(values_list), errors="coerce")
    df = pd.DataFrame({"timestamp": time_list, "value": vals})
    df = df.dropna(subset=["value"])
    if df.empty:
        return df
    df.sort_values("timestamp", inplace=True)
    df = df.drop_duplicates(subset="timestamp", keep="last")
    df.set_index("timestamp", inplace=True)
    return df.resample("1min").ffill()


def _sensor_type(name: str, sensor_states: dict):
    t = sensor_states.get(name, {}).get("type")
    if t:
        return t

    for s in sensors:
        if s[0] == name:
            return s[3]

    for s in read_sensors:
        if s[0] == name:
            return s[3]

    if "consumption" in sensor_states.get(name, {}):
        return "Smart Meter"

    return None


def _latest_interactions_csv():
    """Return the most recently modified interactions.csv in logs/*/interactions.csv."""
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
    """
    Load *simulated* consumption values from the latest interactions.csv.
    No real fallback here: real is loaded in the dual-plot function.
    """
    path = _latest_interactions_csv()
    if not path:
        return {}

    out = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("event_type") == "sensor" and row.get("name") == sensor_name:
                    ts = row.get("timestamp_sim", "")
                    val = row.get("value", "")
                    try:
                        out[ts] = float(val)
                    except Exception:
                        continue
    except Exception:
        return {}

    return out


def _rebase_index_preserve_midnight_rollover(df: pd.DataFrame, ref_day: date) -> pd.DataFrame:
    """
    Rebase df.index to a target day while preserving continuity:
    if time() goes backwards, assume midnight rollover to the next day.
    """
    if df is None or df.empty:
        return df

    df = df.copy()
    times = [ts.time() for ts in df.index]

    day = ref_day
    new_index = []

    prev_t = times[0]
    new_index.append(datetime.combine(day, prev_t))

    for t in times[1:]:
        if t < prev_t:  # midnight rollover
            day = day + timedelta(days=1)
        new_index.append(datetime.combine(day, t))
        prev_t = t

    df.index = new_index
    df = df.sort_index()
    return df


# Plot helpers 
def _apply_common_axes_style(ax, y_label: str):
    """Common axis formatting."""
    ax.set_xlabel("Time")
    ax.set_ylabel(y_label)
    ax.grid(True, linestyle=":", alpha=0.7)

    ax.yaxis.set_major_locator(MaxNLocator(8))
    ax.yaxis.set_major_formatter(FormatStrFormatter("%.1f"))

    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")


def _plot_simple_series(ax, sensor: str, sensor_data: dict, sensor_type: str | None):
    """
    Plot a single simulated series (no real overlay).
    Returns (df_used, y_label).
    """
    time_list = sensor_data.get("time", [])
    state_list = sensor_data.get("state", [])

    # Decide label
    if sensor_type == "Temperature":
        y_label = "Temperature (°C)"
    elif sensor_type in ("PIR", "Switch"):
        y_label = "State"
    else:
        y_label = "Value"

    # Build df
    df = _build_dataframe(time_list, state_list) if (time_list and state_list) else pd.DataFrame()

    if df.empty:
        ax.text(
            0.5,
            0.5,
            "No valid data to plot",
            ha="center",
            va="center",
            transform=ax.transAxes,
        )
        return None, y_label

    unique_vals = set(df["value"].dropna().unique().tolist())
    is_binary = unique_vals.issubset({0.0, 1.0})

    # Step plot for binary sensors (except Smart Meter / Temperature)
    if is_binary and sensor_type not in ("Smart Meter", "Temperature"):
        ax.plot(df.index, df["value"], drawstyle="steps-post", marker="o", linestyle="-", label=sensor)
        ax.set_ylim(-0.1, 1.1)
        ax.set_yticks([0, 1])
    else:
        ax.plot(df.index, df["value"], linestyle="-", linewidth=1.5, marker="o", markersize=2, label=sensor)

    return df, y_label


def _dual_plot_temperature(ax, sensor: str, sensor_data: dict):
    """
    Dual plot for Temperature:
      - simulated series (from sensor_data)
      - real series (from dhtlogger)
    The real series is loaded by label first; if empty, fallback by GPIO binding.
    """
    # --- simulated
    time_list = sensor_data.get("time", [])
    y_sim = sensor_data.get("state", [])
    df_sim = _build_dataframe(time_list, y_sim) if (time_list and y_sim) else pd.DataFrame()

    # --- real: by label first
    df_real = dhtlogger.load_temp_by_label_any_csv(sensor, logs_dir="logs")

    # Fallback: if empty, try by gpio mapping
    if df_real is None or df_real.empty:
        gpio = _get_binding_dht_gpio_for_sensor(sensor)
        if gpio is not None:
            df_real = dhtlogger.load_temp_by_gpio_any_csv(gpio, logs_dir="logs")

    # Nothing to plot
    if (df_sim is None or df_sim.empty) and (df_real is None or df_real.empty):
        ax.text(
            0.5,
            0.5,
            "No data (sim/real) for Temperature",
            ha="center",
            va="center",
            transform=ax.transAxes,
        )
        return None, "Temperature (°C)"

    # Choose a reference day (prefer simulated)
    ref_day = df_sim.index[0].date() if (df_sim is not None and not df_sim.empty) else date(1900, 1, 1)

    # Rebase real index to the simulated day while preserving midnight rollover
    if df_real is not None and not df_real.empty:
        df_real = _rebase_index_preserve_midnight_rollover(df_real, ref_day)

    # Shift simulated to align start time with the real start time (graph-only alignment)
    if (df_sim is not None and not df_sim.empty) and (df_real is not None and not df_real.empty):
        shift = df_real.index[0] - df_sim.index[0]
        df_sim = df_sim.copy()
        df_sim.index = df_sim.index + shift

    # Plot
    if df_sim is not None and not df_sim.empty:
        ax.plot(
            df_sim.index,
            df_sim["value"],
            linestyle="-",
            linewidth=1.5,
            marker="o",
            markersize=2,
            label=f"{sensor} (sim)",
        )
    if df_real is not None and not df_real.empty:
        ax.plot(
            df_real.index,
            df_real["value"],
            linestyle="--",
            linewidth=1.5,
            label=f"{sensor} (real)",
        )

    df_for_date = df_sim if (df_sim is not None and not df_sim.empty) else df_real
    return df_for_date, "Temperature (°C)"


def _dual_plot_smart(ax, sensor: str, sensor_data: dict, sensor_states: dict):
    """
    Dual plot for Smart Meter:
      - simulated series (from sensor_data or interactions.csv)
      - real series (from smartmeter logs by bound IP)
    """
    # simulated
    time_list = sensor_data.get("time", [])
    consumption_list = sensor_data.get("consumption")

    # If no consumption list is present, attempt to rebuild from interactions.csv
    if not consumption_list:
        m = _load_consumption_from_interactions(sensor)
        if m:
            keys = list(m.keys())
            mapped = []
            for t in time_list:
                key = None
                if len(t) == 5:
                    suffix = f" {t}"
                    for k in keys:
                        if k.endswith(suffix):
                            key = k
                            break
                else:
                    if t in m:
                        key = t
                mapped.append(m[key] if key is not None else None)
            consumption_list = mapped

    y_series_sim = _align_len(consumption_list, len(time_list), fill=None) if consumption_list else []
    df_sim = _build_dataframe(time_list, y_series_sim) if y_series_sim else pd.DataFrame()

    # real
    df_real = pd.DataFrame()
    ip_binding = _get_binding_ip_for_sensor(sensor)
    if ip_binding:
        df_real = smartmeter.load_power_by_ip_any_csv(ip_binding, logs_dir="logs")

    if (df_sim is None or df_sim.empty) and (df_real is None or df_real.empty):
        ax.text(
            0.5,
            0.5,
            "No data (sim/real) for Smart Meter",
            ha="center",
            va="center",
            transform=ax.transAxes,
        )
        return None, "Power (W)"

    ref_day = df_sim.index[0].date() if (df_sim is not None and not df_sim.empty) else date(1900, 1, 1)
    if df_real is not None and not df_real.empty:
        df_real = _rebase_index_preserve_midnight_rollover(df_real, ref_day)

    # Shift simulated to align start time with the real start time (graph-only alignment)
    if (df_sim is not None and not df_sim.empty) and (df_real is not None and not df_real.empty):
        shift = df_real.index[0] - df_sim.index[0]
        df_sim = df_sim.copy()
        df_sim.index = df_sim.index + shift

    # Plot
    if df_sim is not None and not df_sim.empty:
        ax.plot(
            df_sim.index,
            df_sim["value"],
            linestyle="-",
            linewidth=1.5,
            marker="o",
            markersize=2,
            label=f"{sensor} (sim)",
        )
    if df_real is not None and not df_real.empty:
        ax.plot(
            df_real.index,
            df_real["value"],
            linestyle="--",
            linewidth=1.5,
            label=f"{sensor} (real)",
        )

    df_for_date = df_sim if (df_sim is not None and not df_sim.empty) else df_real
    return df_for_date, "Power (W)"


# UI functions 
def show_graphs(canvas, sensor_states):
    """
    UI flow:
      1) let the user select sensors
      2) open a new window that shows plots for each selected sensor
    """

    def generate_graph(sensor: str, sensor_data: dict, frame):
        fig, ax = plt.subplots(figsize=(12, 6))

        s_type = _sensor_type(sensor, sensor_states)

        # Dual-plot for Temperature and Smart Meter
        if s_type == "Temperature":
            df_for_date, y_label = _dual_plot_temperature(ax, sensor, sensor_data)
        elif s_type == "Smart Meter":
            df_for_date, y_label = _dual_plot_smart(ax, sensor, sensor_data, sensor_states)
        else:
            df_for_date, y_label = _plot_simple_series(ax, sensor, sensor_data, s_type)

        # Title date (best effort)
        date_str = ""
        try:
            if df_for_date is not None and hasattr(df_for_date, "index") and len(df_for_date.index) > 0:
                date_str = str(df_for_date.index[0].date())
        except Exception:
            date_str = ""

        ax.set_title(f"{sensor} - {date_str}" if date_str else f"{sensor}")
        _apply_common_axes_style(ax, y_label)
        ax.legend()
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
        sc_canvas = tk.Canvas(container)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=sc_canvas.yview)
        scrollable_frame = ttk.Frame(sc_canvas)

        scrollable_frame.bind(
            "<Configure>", lambda e: sc_canvas.configure(scrollregion=sc_canvas.bbox("all"))
        )
        sc_canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        sc_canvas.configure(yscrollcommand=scrollbar.set)

        container.pack(fill="both", expand=True)
        sc_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for s in selected:
            frame = ttk.Frame(scrollable_frame)
            frame.pack(fill="both", pady=10)
            generate_graph(s, sensor_states[s], frame)

    # Selection window
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
        fg="blue",
    ).pack(anchor="w", pady=(0, 5))

    for sensor_name, state in select_sensors.items():
        tk.Checkbutton(selection_window, text=sensor_name, variable=state).pack(anchor="w")

    tk.Button(selection_window, text="Generate Graphs", command=save_selected_logs).pack(pady=10)


def show_graphs_auto(sensor_states, selected_keys, target_frame):
    """
    Render a list of graphs into an existing frame (no selection UI).
    Intended for "auto" dashboards.
    """

    def generate_graph(sensor: str, sensor_data: dict, frame):
        fig, ax = plt.subplots(figsize=(12, 6))

        s_type = _sensor_type(sensor, sensor_states)

        # Smart Meter: dual plot (sim + real)
        if s_type == "Smart Meter":
            df_for_date, y_label = _dual_plot_smart(ax, sensor, sensor_data, sensor_states)
        else:
            # For other sensors, keep the original behavior: plot simulated only.
            df_for_date, y_label = _plot_simple_series(ax, sensor, sensor_data, s_type)

        ax.set_title(f"Sensor trend: {sensor}")
        _apply_common_axes_style(ax, y_label)
        ax.legend()
        fig.tight_layout()

        canvas_plot = FigureCanvasTkAgg(fig, master=frame)
        toolbar = NavigationToolbar2Tk(canvas_plot, frame)
        toolbar.update()
        toolbar.pack(side=tk.TOP, fill=tk.X)

        canvas_plot.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        canvas_plot.draw()
        plt.close(fig)

    # Clear target frame
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
