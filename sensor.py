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
from dhtlogger import load_temp_by_label_any_csv, load_temp_by_gpio_any_csv
import pandas as pd
from collections import deque
from typing import Optional

TEMP_RECENT: dict[str, deque] = {}

sensors = []
add_point_enabled = False

SENSOR_MAP_PATH = "sensor_map.json"

# cache: per ogni sensore -> (lista_tempi_min, lista_valori_C)
TEMP_SERIES: dict[str, tuple[list[float], list[float]]] = {}
# tempo simulato (in "unità" di delta_seconds) per ogni sensore
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
    Carica dal logger DHT la serie di temperatura per questo sensore.
    Usa load_temp_by_label_any_csv(sensor_name), che è lo stesso
    meccanismo che uso per i grafici 'reali'.

    Restituisce:
        (times, values)
    dove:
        times  = minuti relativi dal primo campione (float)
        values = temperatura in °C (float)
    oppure None se non c'è niente.
    """
    if not sensor_name:
        TEMP_SERIES[sensor_name] = None
        return None

    # cache già caricata
    if sensor_name in TEMP_SERIES:
        return TEMP_SERIES[sensor_name]

    # 1) prova per label 
    df = None
    try:
        df = load_temp_by_label_any_csv(sensor_name)
    except Exception as e:
        print(f"[TEMP] load_temp_by_label_any_csv fallita per {sensor_name}: {e}")

    # 2) sennò fai per GPIO
    if (df is None or df.empty or "value" not in df.columns):
        mapping = _load_sensor_map()
        cfg = mapping.get(sensor_name, {})
        if isinstance(cfg, dict) and cfg.get("by") == "dht":
            gpio = cfg.get("gpio")
            if gpio is not None:
                try:
                    df = load_temp_by_gpio_any_csv(int(gpio))
                except Exception as e:
                    print(f"[TEMP] load_temp_by_gpio_any_csv fallita per {sensor_name}: {e}")

    if df is None or df.empty or "value" not in df.columns:
        print(f"[TEMP] nessuna serie trovata per {sensor_name}")
        TEMP_SERIES[sensor_name] = None
        return None

    if df is None or df.empty or "value" not in df.columns:
        print(f"[TEMP] nessuna serie trovata per {sensor_name}")
        TEMP_SERIES[sensor_name] = None
        return None

    # tieni solo valori validi (togli quelli che non legge a causa di bug e altri fattori)
    df = df.dropna(subset=["value"])
    if df.empty:
        print(f"[TEMP] solo NaN per {sensor_name}")
        TEMP_SERIES[sensor_name] = None
        return None

    # faccia in modo tale che sia ordinato temporalmente
    if not isinstance(df.index, pd.DatetimeIndex):
        try:
            df.index = pd.to_datetime(df.index, errors="coerce")
        except Exception as e:
            print(f"[TEMP] impossibile convertire indice in datetime per {sensor_name}: {e}")
    df = df.sort_index()

    if df.empty:
        TEMP_SERIES[sensor_name] = None
        return None

    # minuti relativi dal primo campione
    t0 = df.index[0]
    rel_minutes = (df.index - t0).total_seconds() / 60.0
    times = rel_minutes.to_list()
    values = df["value"].astype(float).to_list()

    TEMP_SERIES[sensor_name] = (times, values)
    print(
        f"[TEMP] {sensor_name}: {len(times)} campioni "
        f"da {df.index[0]} a {df.index[-1]}"
    )
    return TEMP_SERIES[sensor_name]

def _get_intraday_pattern(sensor_name: str, time_of_day_minutes: float, window_days: int = 7) -> Optional[float]:
    """
    Usa i dati storici per trovare il pattern nella giornata.
    
    Cerca in tutti i giorni precedenti qual è il valore tipico a questa ora del giorno,
    e ritorna la media ponderata (più recente = più peso).
    
    Args:
        sensor_name: nome del sensore
        time_of_day_minutes: minuti dalla mezzanotte (0-1440)
        window_days: quanti giorni precedenti analizzare
    
    Returns:
        valore predetto o None se non ci sono dati
    """
    df = None
    mapping = _load_sensor_map()
    cfg = mapping.get(sensor_name, {})
    
    # Prova caricamento
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
    
    # Aggiungi colonna con ora del giorno (minuti dalla mezzanotte) e giorno
    if not isinstance(df.index, pd.DatetimeIndex):
        return None
    
    df_copy = df.copy()
    df_copy["hour_of_day"] = (df_copy.index.hour * 60 + df_copy.index.minute).astype(float)
    df_copy["day"] = df_copy.index.date
    
    # Trova valori simili (entro ±30 minuti dall'ora desiderata)
    tolerance_min = 30
    candidates = df_copy[
        (df_copy["hour_of_day"] >= time_of_day_minutes - tolerance_min) &
        (df_copy["hour_of_day"] <= time_of_day_minutes + tolerance_min)
    ]
    
    if candidates.empty:
        return None
    
    # Calcola la media ponderata (giorni più recenti hanno peso maggiore)
    unique_days = candidates["day"].unique()
    if len(unique_days) > window_days:
        unique_days = unique_days[-window_days:]
    
    weighted_sum = 0.0
    weight_total = 0.0
    
    for idx, day in enumerate(unique_days):
        day_data = candidates[candidates["day"] == day]["value"]
        if not day_data.empty:
            day_mean = float(day_data.mean())
            # Peso: giorni più recenti hanno peso maggiore (exponential)
            weight = 2.0 ** (idx - len(unique_days) + 1)
            weighted_sum += day_mean * weight
            weight_total += weight
    
    if weight_total > 0:
        return weighted_sum / weight_total
    
    return None


def get_replay_temperature(sensor_name: str, sim_minutes: float):
    """
    Replay della temperatura basato su dati storici.
    
    Logica:
    - Se sim_minutes è entro i dati disponibili -> replay diretto (interpolazione)
    - Se sim_minutes è oltre i dati -> usa il pattern intraday per predire
    """
    series = _load_temp_series_for_sensor(sensor_name)
    if not series:
        return None

    times, values = series
    if not times or not values:
        return None

    # Before first
    if sim_minutes <= times[0]:
        return float(values[0])

    # All'interno del range disponibile: interpolazione diretta
    if sim_minutes <= times[-1]:
        for i in range(len(times) - 1):
            t1, t2 = times[i], times[i + 1]
            if t1 <= sim_minutes <= t2:
                v1, v2 = values[i], values[i + 1]
                if t2 == t1:
                    return float(v1)
                alpha = (sim_minutes - t1) / (t2 - t1)
                return float(v1 + alpha * (v2 - v1))
        return float(values[-1])
    
    # AFTER LAST: usa il pattern intraday per predire
    # Calcola a che ora della giornata siamo (in minuti dalla mezzanotte)
    total_series_minutes = times[-1]
    time_of_day = (total_series_minutes % (24 * 60))  # modulo 1440 (minuti in un giorno)
    
    # Prova a trovare un pattern storico
    predicted = _get_intraday_pattern(sensor_name, time_of_day)
    if predicted is not None:
        return float(predicted)
    
    # Fallback: usa l'ultimo valore noto + damping leggero
    last_val = float(values[-1])
    
    # Calcola la pendenza degli ultimi valori per damping
    if len(values) >= 3:
        v_prev = float(values[-2])
        v_last = float(values[-1])
        slope = v_last - v_prev
        # Dampizza la pendenza per extrapolazione
        steps_beyond = sim_minutes - times[-1]
        damped_slope = slope * (0.95 ** steps_beyond)  # exponential decay
        return max(15.0, min(40.0, last_val + damped_slope))
    
    return last_val

def get_sensor_params(sensor_type):
    params = {
        "PIR": {"min": 0.0, "max": 1.0, "step": 1.0, "state": 0.0, "direction": 0, "consumption": None},
        "Temperature": {"min": 18.0, "max": 35.0, "step": 0.5, "state": 18.0, "direction": None, "consumption": None},
        "Switch": {"min": 0, "max": 1, "step": 1, "state": 0, "direction": None, "consumption": None},
        "Smart Meter": {"min": 0.0, "max": 5000.0, "step": 10.0, "state": 0.0, "direction": None, "consumption": 0.0},
        "Weight": {"min": 0.0, "max": 1.0, "step": 1.0, "state": 0.0, "direction": None, "consumption": None},
    }
    return params.get(
        sensor_type,
        {"min": 0.0, "max": 1.0, "step": 1.0, "state": 0.0, "direction": None, "consumption": None},
    )


def _last_slope_deg_per_min(series) -> float:
    """Ritorna la pendenza finale in °C/min (ultima differenza)."""
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
        sensor = (
            name,
            x,
            y,
            type,
            float(min_val),
            float(max_val),
            float(step),
            float(state),
            direction,
            consumption,
            associated_device,
        )

        # write to the right list according to load_active
        if load_active:
            sensors_file.append(sensor)
        else:
            sensors.append(sensor)

        draw_sensor(canvas, sensor)


def changePIR(canvas, sensor, sensors, new_state=None):
    if len(sensor) != 11:
        print(f"Error: wrong sensor structure {sensor}")
        return None, None, sensors

    name, x, y, type, min_val, max_val, step, state, direction, consumption, associated_device = sensor
    state = float(state)

    if new_state is None:
        new_state = 1 if state == 0 else 0

    updated_sensors = []
    for s in sensors:
        if s == sensor:
            updated_sensors.append(
                (
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
            )
        elif s[3] == "PIR":
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

    # 1) se è associato a DHT via GPIO, prova per gpio
    if isinstance(cfg, dict) and cfg.get("by") == "dht":
        gpio = cfg.get("gpio")
        if gpio is not None:
            try:
                df = load_temp_by_gpio_any_csv(int(gpio))
            except Exception as e:
                print(f"[WARN] load_temp_by_gpio_any_csv failed for {sensor_name}: {e}")

    # 2) sennò cerca nel file
    if df is None or df.empty:
        try:
            df = load_temp_by_label_any_csv(sensor_name)
        except Exception as e:
            print(f"[WARN] load_temp_by_label_any_csv failed for {sensor_name}: {e}")
            df = None

    if df is None or df.empty or "value" not in df.columns:
        return None

    # tieni solo i valori numerici validi
    df_valid = df.dropna(subset=["value"])
    if df_valid.empty:
        return None

    # ULTIMO valore (cioè "in quel minuto")
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

def changeTemperature(canvas, sensor, sensors, heating_factor, delta_seconds, current_datetime=None):
    """
    Update a Temperature sensor state.

    Behavior:
      - If no real CSV exists for this sensor: simple physical-ish update.
      - If CSV exists:
          * within CSV horizon: replay (interpolation)
          * beyond CSV: damped extrapolation (no ML; temp_forecast module not present)
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
    sim_min = prev_sim_min + delta_sim_min
    TEMP_SIM_MIN[name] = sim_min

    # Load real series (if available)
    series = _load_temp_series_for_sensor(name)
    last_real_min = None
    if series:
        times, _values = series
        if times:
            last_real_min = float(times[-1])

    # Keep a recent buffer (useful if you re-enable ML later)
    recent = TEMP_RECENT.get(name)
    if recent is None:
        recent = deque(maxlen=30)  # last 30 minutes
        TEMP_RECENT[name] = recent
    recent.append(float(current_state))

    new_state = float(current_state)

    # --- If no CSV exists -> fallback "physics"
    if last_real_min is None:
        if heating_factor > 0:
            new_state = current_state + step * delta_sim_min * float(heating_factor)
        else:
            new_state = current_state - step * delta_sim_min

        new_state = max(min_val, min(max_val, new_state))
        new_state = round(new_state * 2) / 2.0

    else:
        # --- CSV exists -> replay / extrapolate using get_replay_temperature()
        target = get_replay_temperature(name, sim_min)
        if target is not None:
            new_state = float(target)

        # Do NOT clamp to min/max when replaying real data
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

    changing = abs(new_state - current_state) > 1e-6
    update_temperature_sensor_color(canvas, name, changing=changing)
    return name, new_state, updated


def _get_intraday_power_pattern(associated_device: str, time_of_day_minutes: float, window_days: int = 7) -> Optional[float]:
    """
    Usa i dati storici dello smart meter per trovare il pattern di consumo nella giornata.
    
    Cerca in tutti i giorni precedenti qual è il consumo tipico a questa ora del giorno,
    e ritorna la media ponderata (più recente = più peso).
    
    Args:
        associated_device: nome del dispositivo associato (pc, wm, dw, etc.)
        time_of_day_minutes: minuti dalla mezzanotte (0-1440)
        window_days: quanti giorni precedenti analizzare
    
    Returns:
        potenza predetta in Watt o None se non ci sono dati
    """
    try:
        import smartmeter
    except ImportError:
        return None
    
    # Deriva il device_id dal nome
    device_id = smartmeter.derive_device_id(associated_device)
    
    # Carica i dati storici
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
    
    # Aggiungi colonna con ora del giorno (minuti dalla mezzanotte) e giorno
    if not isinstance(df.index, pd.DatetimeIndex):
        return None
    
    df_copy = df.copy()
    df_copy["hour_of_day"] = (df_copy.index.hour * 60 + df_copy.index.minute).astype(float)
    df_copy["day"] = df_copy.index.date
    
    # Trova valori simili (entro ±30 minuti dall'ora desiderata)
    tolerance_min = 30
    candidates = df_copy[
        (df_copy["hour_of_day"] >= time_of_day_minutes - tolerance_min) &
        (df_copy["hour_of_day"] <= time_of_day_minutes + tolerance_min)
    ]
    
    if candidates.empty:
        return None
    
    # Calcola la media ponderata (giorni più recenti hanno peso maggiore)
    unique_days = candidates["day"].unique()
    if len(unique_days) > window_days:
        unique_days = unique_days[-window_days:]
    
    weighted_sum = 0.0
    weight_total = 0.0
    
    for idx, day in enumerate(unique_days):
        day_data = candidates[candidates["day"] == day]["value"]
        if not day_data.empty:
            day_mean = float(day_data.mean())
            # Peso: giorni più recenti hanno peso maggiore (exponential)
            weight = 2.0 ** (idx - len(unique_days) + 1)
            weighted_sum += day_mean * weight
            weight_total += weight
    
    if weight_total > 0:
        return weighted_sum / weight_total
    
    return None


def get_replay_smart_meter_consumption(associated_device: str, sim_minutes: float) -> Optional[float]:
    """
    Replay del consumo dello smart meter basato su dati storici.
    
    Logica:
    - Se sim_minutes è entro i dati disponibili -> replay diretto (interpolazione)
    - Se sim_minutes è oltre i dati -> usa il pattern intraday per predire
    """
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
    
    # Converti in minuti relativi dal primo valore
    t0 = df_sorted.index[0]
    rel_minutes = (df_sorted.index - t0).total_seconds() / 60.0
    times = rel_minutes.to_list()
    values = df_sorted["value"].astype(float).to_list()
    
    # Before first
    if sim_minutes <= times[0]:
        return float(values[0])
    
    # All'interno del range disponibile: interpolazione diretta
    if sim_minutes <= times[-1]:
        for i in range(len(times) - 1):
            t1, t2 = times[i], times[i + 1]
            if t1 <= sim_minutes <= t2:
                v1, v2 = values[i], values[i + 1]
                if t2 == t1:
                    return float(v1)
                alpha = (sim_minutes - t1) / (t2 - t1)
                return float(v1 + alpha * (v2 - v1))
        return float(values[-1])
    
    # AFTER LAST: usa il pattern intraday per predire
    total_series_minutes = times[-1]
    time_of_day = (total_series_minutes % (24 * 60))
    
    # Prova a trovare un pattern storico
    predicted = _get_intraday_power_pattern(associated_device, time_of_day)
    if predicted is not None:
        return float(predicted)
    
    # Fallback: usa l'ultimo valore noto
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
        # 1) Prova prima la predizione intelligente basata su dati storici
        if current_datetime:
            # Calcola i minuti simulati dalla mezzanotte di quella giornata
            time_of_day = (current_datetime.hour * 60 + current_datetime.minute)
            # Calcola quanti giorni abbiamo simulato (possiamo fare cumulative, ma per ora prendiamo solo l'ora)
            sim_minutes = time_of_day
            
            # Prova la predizione storica
            replay_consumption = get_replay_smart_meter_consumption(associated_device, sim_minutes)
            if replay_consumption is not None:
                new_consumption = max(0.0, float(replay_consumption))
            else:
                # 2) Fallback: calcola dal dispositivo associato e cicli attivi
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
            # Se non abbiamo current_datetime, usa il metodo vecchio
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

        devices_names_runtime = [d[0] for d in devices] if devices else []
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

        for s in sensors + list(sensors_file):
            if name == s[0]:
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
            # se esiste la serie dal CSV, usa il primo valore reale come stato iniziale
            series = _load_temp_series_for_sensor(name)
            if series:
                _, vals = series
                if vals:
                    real_temp = float(vals[0])
                    params["state"] = max(params["min"], min(params["max"], real_temp))

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