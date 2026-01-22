# dhtlogger.py
from __future__ import annotations
import os, csv, time, threading, logging, glob
from typing import Optional, Tuple
from datetime import datetime
import pandas as pd

logger = logging.getLogger("dht")
logger.setLevel(logging.INFO)

_USE_CPY = False
_USE_ADA = False
try:
    import board, adafruit_dht
    _USE_CPY = True
except Exception:
    try:
        import Adafruit_DHT as ADA_DHT
        _USE_ADA = True
    except Exception:
        pass

LOGGERS: dict[str, "DHTLogger"] = {}
DEFAULT_INTERVAL = 60

# sanitize label for filesystem use
def _sanitize(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in (name or "").strip())

def csv_path_for_label(label: str) -> str:
    safe = _sanitize(label or "sensor")
    return os.path.join("logs", f"dht_{safe}.csv")

def csv_ensure_header(path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if not os.path.isfile(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["timestamp_iso", "label", "gpio", "temp_C", "hum_%"])

# mapping BCM -> board pin
def _board_pin_from_bcm(gpio: int):
    if not _USE_CPY:
        return None
    return {
        4: getattr(board, "D4", None),
        17: getattr(board, "D17", None),
        27: getattr(board, "D27", None),
        22: getattr(board, "D22", None),
        5: getattr(board, "D5", None),
        6: getattr(board, "D6", None),
        13: getattr(board, "D13", None),
        19: getattr(board, "D19", None),
        26: getattr(board, "D26", None),
    }.get(int(gpio), None)

#DHT Logger Class 
class DHTLogger:
    def __init__(self, sensor_label: str, gpio_bcm: int, interval: int = DEFAULT_INTERVAL):
        self.label = str(sensor_label)
        self.gpio = int(gpio_bcm)        
        self.interval = max(1, int(interval))
        self.csv_path = csv_path_for_label(self.label)
        self._stop = threading.Event()
        self._t: Optional[threading.Thread] = None

        self._dht_device = None  
        csv_ensure_header(self.csv_path)

        if _USE_CPY:
            pin = _board_pin_from_bcm(self.gpio)
            if pin is None:
                logger.warning("Cannot map GPIO %s to a board pin; will use Adafruit_DHT if avaiable.", self.gpio)
            else:
                try:
                    self._dht_device = adafruit_dht.DHT22(pin, use_pulseio=False)
                except Exception as e:
                    logger.warning("Init adafruit_dht failed (%s); will try fallback Adafruit_DHT if avaiable.", e)

    def start(self):
        if self._t and self._t.is_alive():
            logger.info("DHTLogger '%s' already active", self.label)
            return
        self._stop.clear()
        self._t = threading.Thread(target=self._run, daemon=True, name=f"DHT-{self.label}")
        self._t.start()
        logger.info("DHTLogger '%s' started (GPIO BCM=%s)", self.label, self.gpio)

    def stop(self):
        if not self._t:
            return
        self._stop.set()
        self._t.join(timeout=2)
        logger.info("DHTLogger '%s' stopped", self.label)

    def _read_once(self) -> Tuple[Optional[float], Optional[float]]:
        # CircuitPython DHT
        if _USE_CPY and self._dht_device is not None:
            try:
                t = self._dht_device.temperature
                h = self._dht_device.humidity
                return (float(t) if t is not None else None,
                        float(h) if h is not None else None)
            except RuntimeError:
                return (None, None)
            except Exception as e:
                logger.warning("[DHT '%s'] error CircuitPython: %s", self.label, e)
                return (None, None)

        # Adafruit_DHT
        if _USE_ADA:
            try:
                h, t = ADA_DHT.read_retry(ADA_DHT.DHT22, self.gpio)
                t = float(t) if t is not None else None
                h = float(h) if h is not None else None
                return (t, h)
            except Exception as e:
                logger.warning("[DHT '%s'] errore Adafruit_DHT: %s", self.label, e)
                return (None, None)

        # No librery available
        logger.warning("[DHT '%s'] no DHT library avaiable in this host", self.label)
        return (None, None)

    def _run(self):
        while not self._stop.is_set():
            try:
                t, h = self._read_once()
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
                    csv.writer(f).writerow([ts, self.label, self.gpio,
                                            "" if t is None else t,
                                            "" if h is None else h])
            except Exception as e:
                logger.warning("[DHT '%s'] loop error: %s", self.label, e)
            for _ in range(self.interval):
                if self._stop.is_set():
                    break
                time.sleep(1)


# ---------- Manager functions ----------
def start_dht_logger(sensor_label: str, gpio: int, interval: int = DEFAULT_INTERVAL) -> DHTLogger:
    """start a DHT logger for the given sensor label and GPIO BCM pin."""
    lab = str(sensor_label)
    lg = LOGGERS.get(lab)
    if lg is None:
        lg = DHTLogger(sensor_label=lab, gpio_bcm=int(gpio), interval=interval)
        LOGGERS[lab] = lg
    lg.start()
    return lg

def stop_dht_logger(sensor_label: str):
    lg = LOGGERS.pop(str(sensor_label), None)
    if lg:
        lg.stop()

def stop_all():
    for k in list(LOGGERS.keys()):
        stop_dht_logger(k)


# ---------- Loader (for graphs) ----------
def _df_from_rows(rows: list[dict]) -> pd.DataFrame:
    """load a DataFrame from a list of rows with 'timestamp' and 'value' keys."""
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").set_index("timestamp")
    return df.resample("1min").median()


def load_temp_by_label_any_csv(label: str, logs_dir="logs") -> pd.DataFrame:
    """Load temperature data for the given sensor label from any CSV in logs_dir."""
    path = os.path.join(logs_dir, f"dht_{_sanitize(label)}.csv")
    rows = []
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                val = row.get("temp_C")
                try:
                    rows.append({"timestamp": row.get("timestamp_iso"), "value": float(val)})
                except Exception:
                    continue
    return _df_from_rows(rows)

#fallback
def load_temp_by_gpio_any_csv(gpio: int, logs_dir="logs") -> pd.DataFrame:
    """load temperature data for the given GPIO BCM pin from any CSV in logs_dir."""
    rows = []
    for path in glob.glob(os.path.join(logs_dir, "dht_*.csv")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                r = csv.DictReader(f)
                for row in r:
                    try:
                        if int(row.get("gpio")) != int(gpio):
                            continue
                    except Exception:
                        continue
                    try:
                        rows.append({"timestamp": row.get("timestamp_iso"), "value": float(row.get("temp_C"))})
                    except Exception:
                        continue
        except Exception:
            continue
    return _df_from_rows(rows)
