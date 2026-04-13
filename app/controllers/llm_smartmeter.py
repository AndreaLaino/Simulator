from __future__ import annotations

import copy
import importlib.util
import json
import math
import re
import shutil
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.context import AppContext
from app.logging_setup import setup_logging
from app.save_paths import get_or_create_current_save_session

logger = setup_logging("controllers.llm_smartmeter")

BASE_DIR = Path(__file__).resolve().parents[2]
LLM_SM_DIR = BASE_DIR / "LLM" / "smartmeter"

APPLIANCE_OPTIONS = {
    "Computer": {
        "key": "computer",
        "folder": "Computer",
        "output": "computer_case_output",
    },
    "Coffee Machine": {
        "key": "coffee_machine",
        "folder": "Coffee_machine",
        "output": "coffee_machine_output",
    },
    "Dishwasher": {
        "key": "dishwasher",
        "folder": "Dishwasher",
        "output": "dishwasher_output",
    },
    "Refrigerator": {
        "key": "refrigerator",
        "folder": "Refrigerator",
        "output": "refrigerator_output",
    },
    "Washing Machine": {
        "key": "washing_machine",
        "folder": "Washing_machine",
        "output": "washing_machine_output",
    },
}


@dataclass
class LlmRunResult:
    chosen_k: int
    appliance_key: str
    selected_cycle_id: int
    dominant_cluster: int
    dominant_cluster_n_cycles: int
    selected_cluster: int
    output_dir: Path
    cases_eval_csv: Path
    selected_case_csv: Path


def _upsert_runtime_profile(
    appliance_key: str,
    output_dir: Path,
    chosen_k: int,
    dominant_cluster: int,
    dominant_cluster_n_cycles: int,
    selected_cluster: int,
    selected_cycle_id: int,
) -> Path:
    """Persist the latest LLM selection so runtime smart meters can replay it."""
    catalog_path = LLM_SM_DIR / "llm_smartmeter_profiles.json"
    payload = {
        "appliance_key": appliance_key,
        "output_dir": str(output_dir),
        "chosen_k": int(chosen_k),
        "dominant_cluster": int(dominant_cluster),
        "dominant_cluster_n_cycles": int(dominant_cluster_n_cycles),
        "selected_cluster": int(selected_cluster),
        "selected_cycle_id": int(selected_cycle_id),
        "pkl_path": str(output_dir / "llm_runtime_cycles.pkl"),
        "updated_at": pd.Timestamp.now().isoformat(),
    }

    existing: dict = {}
    if catalog_path.exists():
        try:
            existing = json.loads(catalog_path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except Exception:
            existing = {}

    existing[appliance_key] = payload
    catalog_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return catalog_path


def _load_module_from_file(module_name: str, module_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, str(module_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module spec for {module_name} from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_optimal_k_report(report_path: Path) -> dict[str, int]:
    txt = report_path.read_text(encoding="utf-8")
    out: dict[str, int] = {}

    patterns = {
        "k_vote": r"Cluster suggerito \(voto\):\s*(\d+)",
        "k_human": r"Cluster human-friendly:\s*(\d+)",
        "k_accel": r"Elbow acceleration\s*->\s*k\s*=\s*(\d+)",
        "k_sil": r"Silhouette\s*->\s*k\s*=\s*(\d+)",
        "k_db": r"Davies-Bouldin\s*->\s*k\s*=\s*(\d+)",
        "k_ch": r"Calinski-Harabasz\s*->\s*k\s*=\s*(\d+)",
    }

    for key, pattern in patterns.items():
        m = re.search(pattern, txt)
        if m:
            out[key] = int(m.group(1))

    return out


def _load_time_value_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)

    candidates = [
        ("time", "value"),
        ("timestamp_iso", "power_W"),
        ("timestamp_sim", "value"),
        ("timestamp", "value"),
    ]

    time_col = None
    value_col = None
    for t_col, v_col in candidates:
        if t_col in df.columns and v_col in df.columns:
            time_col, value_col = t_col, v_col
            break

    if time_col is None or value_col is None:
        raise ValueError(
            "CSV must contain one valid pair of columns: "
            "(time,value), (timestamp_iso,power_W), (timestamp_sim,value), (timestamp,value)."
        )

    out = pd.DataFrame(
        {
            "time": pd.to_datetime(df[time_col], errors="coerce"),
            "value": pd.to_numeric(df[value_col], errors="coerce"),
        }
    )
    out = out.dropna(subset=["time", "value"]).sort_values("time").reset_index(drop=True)

    if out.empty:
        raise ValueError("CSV has no valid rows after time/value parsing.")

    return out


def _get_last_incomplete_day(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["day"] = d["time"].dt.date

    grouped = d.groupby("day", as_index=False).agg(last_time=("time", "max"), n=("time", "count"))
    grouped = grouped.sort_values("day", ascending=False).reset_index(drop=True)

    for _, row in grouped.iterrows():
        last_ts = pd.Timestamp(row["last_time"])
        # Conservative heuristic for "unfinished day".
        if last_ts.hour < 23:
            day = row["day"]
            return d[d["day"] == day].sort_values("time").reset_index(drop=True)

    # Fallback: latest day in file.
    day = grouped.iloc[0]["day"]
    return d[d["day"] == day].sort_values("time").reset_index(drop=True)


def _energy_kwh(df: pd.DataFrame) -> float:
    if len(df) < 2:
        return 0.0
    t_sec = (df["time"] - df["time"].iloc[0]).dt.total_seconds().to_numpy(dtype=float)
    p = df["value"].to_numpy(dtype=float)
    if len(t_sec) < 2:
        return 0.0
    dt_h = (t_sec[1:] - t_sec[:-1]) / 3600.0
    p_avg = (p[1:] + p[:-1]) / 2.0
    wh = float((p_avg * dt_h).sum())
    return wh / 1000.0


def _feature_vector(df: pd.DataFrame) -> dict[str, float]:
    if df.empty:
        return {
            "duration_minutes": 0.0,
            "max_power": 0.0,
            "mean_power": 0.0,
            "energy_kwh": 0.0,
            "time_of_peak_norm": 0.0,
        }

    start = pd.Timestamp(df["time"].iloc[0])
    end = pd.Timestamp(df["time"].iloc[-1])
    duration_minutes = max(0.0, (end - start).total_seconds() / 60.0)

    values = df["value"].to_numpy(dtype=float)
    max_power = float(values.max()) if len(values) else 0.0
    mean_power = float(values.mean()) if len(values) else 0.0
    energy_kwh = _energy_kwh(df)

    t_sec = (df["time"] - start).dt.total_seconds().to_numpy(dtype=float)
    if len(t_sec) > 0 and t_sec[-1] > 1e-9:
        peak_idx = int(values.argmax())
        peak_norm = float(t_sec[peak_idx] / t_sec[-1])
    else:
        peak_norm = 0.0

    return {
        "duration_minutes": duration_minutes,
        "max_power": max_power,
        "mean_power": mean_power,
        "energy_kwh": energy_kwh,
        "time_of_peak_norm": peak_norm,
    }


def _compute_case_distances(rep_df: pd.DataFrame, partial_features: dict[str, float]) -> pd.DataFrame:

    cols = ["duration_minutes", "max_power", "mean_power", "energy_kwh", "time_of_peak_norm"]
    work = rep_df.copy()
    # Ensure all required columns exist; fill with NaN if missing
    for c in cols:
        if c not in work.columns:
            work[c] = float('nan')
        work[c] = pd.to_numeric(work[c], errors="coerce")

    std = work[cols].std(ddof=0).replace(0, 1).fillna(1)

    def row_distance(row: pd.Series) -> float:
        acc = 0.0
        used = 0
        for c in cols:
            v = row.get(c)
            if pd.isna(v):
                continue
            z = (float(v) - float(partial_features[c])) / float(std[c])
            acc += z * z
            used += 1
        return math.sqrt(acc / max(1, used))

    work["distance_to_last_incomplete_day"] = work.apply(row_distance, axis=1)
    work = work.sort_values("distance_to_last_incomplete_day", kind="mergesort").reset_index(drop=True)
    return work


def _run_llm_pipeline(
    appliance_label: str,
    csv_path: Path,
    k_mode: str,
    custom_k: int | None,
    custom_params_path: str | None,
) -> LlmRunResult:
    common_dir = LLM_SM_DIR / "common"
    cycle_pipeline_mod = _load_module_from_file(
        "llm_smartmeter_cycle_pipeline", common_dir / "cycle_pipeline.py"
    )
    params_mod = _load_module_from_file(
        "llm_smartmeter_params", common_dir / "params.py"
    )
    run_cycle_pipeline = cycle_pipeline_mod.run_cycle_pipeline
    load_appliance_config = params_mod.load_appliance_config

    cfg = APPLIANCE_OPTIONS[appliance_label]
    appliance_key = cfg["key"]
    # Canonical modular output per appliance (stable location expected by users/tools).
    output_dir = LLM_SM_DIR / cfg["folder"] / cfg["output"]
    output_dir.mkdir(parents=True, exist_ok=True)

    # Keep a session-scoped mirror as historical archive.
    session_dir = get_or_create_current_save_session(suffix="llm")
    session_output_dir = session_dir / "llm" / cfg["folder"] / cfg["output"]
    session_output_dir.mkdir(parents=True, exist_ok=True)

    df = _load_time_value_csv(csv_path)
    runtime_tsv = output_dir / "llm_runtime_input.tsv"
    df[["time", "value"]].to_csv(runtime_tsv, sep="\t", index=False)

    params = copy.deepcopy(load_appliance_config(appliance_key, custom_path=custom_params_path))
    params.setdefault("cycle", {})["force_rebuild_pkl"] = True

    exact_k = custom_k if k_mode == "custom" else None

    run_cycle_pipeline(
        input_path=runtime_tsv,
        output_dir=output_dir,
        pkl_path=output_dir / "llm_runtime_cycles.pkl",
        chart_title_prefix=appliance_key,
        params=params,
        exact_k=exact_k,
    )

    # Mirror latest canonical outputs into the current session archive.
    for item in output_dir.iterdir():
        dst = session_output_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dst)

    if k_mode == "custom":
        chosen_k = int(custom_k)
    else:
        picks = _parse_optimal_k_report(output_dir / "optimal_k_report.txt")
        if k_mode == "human":
            chosen_k = int(picks.get("k_human") or picks.get("k_vote") or picks.get("k_sil") or 2)
        else:
            chosen_k = int(picks.get("k_vote") or picks.get("k_human") or picks.get("k_sil") or 2)

    cases_dir = output_dir / f"results_k{chosen_k}"
    rep_path = cases_dir / "cluster_representatives.csv"
    clusters_path = cases_dir / "clusters.csv"
    summary_path = cases_dir / "cluster_summary.csv"
    if not rep_path.exists():
        raise FileNotFoundError(f"Cases file not found for chosen k: {rep_path}")
    if not clusters_path.exists():
        raise FileNotFoundError(f"Clusters file not found for chosen k: {clusters_path}")
    if not summary_path.exists():
        raise FileNotFoundError(f"Cluster summary file not found for chosen k: {summary_path}")

    rep_df = pd.read_csv(rep_path)
    if rep_df.empty:
        raise RuntimeError("No generated cases found in cluster_representatives.csv.")

    clusters_df = pd.read_csv(clusters_path)
    if clusters_df.empty:
        raise RuntimeError("No generated cycles found in clusters.csv.")

    summary_df = pd.read_csv(summary_path)
    if summary_df.empty or "cluster" not in summary_df.columns or "n_cycles" not in summary_df.columns:
        raise RuntimeError("Invalid or empty cluster_summary.csv for chosen k.")

    summary_df["cluster"] = pd.to_numeric(summary_df["cluster"], errors="coerce")
    summary_df["n_cycles"] = pd.to_numeric(summary_df["n_cycles"], errors="coerce")
    summary_df = summary_df.dropna(subset=["cluster", "n_cycles"])
    if summary_df.empty:
        raise RuntimeError("cluster_summary.csv has no valid cluster sizes.")

    summary_df = summary_df.sort_values(["n_cycles", "cluster"], ascending=[False, True], kind="mergesort")
    dominant_cluster = int(summary_df.iloc[0]["cluster"])
    dominant_cluster_n_cycles = int(summary_df.iloc[0]["n_cycles"])

    partial_df = _get_last_incomplete_day(df)
    partial_features = _feature_vector(partial_df)

    ranked = _compute_case_distances(clusters_df, partial_features)
    ranked.insert(0, "chosen_k", chosen_k)
    ranked.insert(1, "dominant_cluster", dominant_cluster)
    ranked.insert(2, "dominant_cluster_n_cycles", dominant_cluster_n_cycles)
    for feat_name, feat_value in partial_features.items():
        ranked[f"last_day_{feat_name}"] = float(feat_value)

    cases_eval_csv = output_dir / f"cases_evaluation_k{chosen_k}.csv"
    ranked.to_csv(cases_eval_csv, index=False)

    dominant_ranked = ranked[ranked["cluster"] == dominant_cluster].copy().reset_index(drop=True)
    if not dominant_ranked.empty:
        feat_cols = ["duration_minutes", "max_power", "mean_power", "energy_kwh", "time_of_peak_norm"]
        for c in feat_cols:
            dominant_ranked[c] = pd.to_numeric(dominant_ranked[c], errors="coerce")

        center = dominant_ranked[feat_cols].mean(skipna=True)
        std = dominant_ranked[feat_cols].std(ddof=0).replace(0, 1).fillna(1)

        def _center_dist(row: pd.Series) -> float:
            acc = 0.0
            used = 0
            for c in feat_cols:
                v = row.get(c)
                if pd.isna(v) or pd.isna(center[c]):
                    continue
                z = (float(v) - float(center[c])) / float(std[c])
                acc += z * z
                used += 1
            return math.sqrt(acc / max(1, used))

        dominant_ranked["distance_to_cluster_center"] = dominant_ranked.apply(_center_dist, axis=1)
        dominant_ranked["selection_score"] = (
            dominant_ranked["distance_to_last_incomplete_day"]
            + 0.35 * dominant_ranked["distance_to_cluster_center"]
        )
        dominant_ranked = dominant_ranked.sort_values(
            ["selection_score", "distance_to_last_incomplete_day"],
            ascending=[True, True],
            kind="mergesort",
        ).reset_index(drop=True)
        best = dominant_ranked.iloc[[0]].copy()
    else:
        best = ranked.iloc[[0]].copy()
    selected_case_csv = output_dir / "selected_case_latest_incomplete_day.csv"
    best.to_csv(selected_case_csv, index=False)

    selected_cluster = int(best.iloc[0]["cluster"])
    selected_cycle_id = int(best.iloc[0]["cycle_id"])

    catalog_path = _upsert_runtime_profile(
        appliance_key=appliance_key,
        output_dir=output_dir,
        chosen_k=chosen_k,
        dominant_cluster=dominant_cluster,
        dominant_cluster_n_cycles=dominant_cluster_n_cycles,
        selected_cluster=selected_cluster,
        selected_cycle_id=selected_cycle_id,
    )
    logger.info(
        "[LLM SmartMeter] profile updated for %s (k=%s, dominant_cluster=%s, selected_cluster=%s, cycle_id=%s) -> %s",
        appliance_key,
        chosen_k,
        dominant_cluster,
        selected_cluster,
        selected_cycle_id,
        catalog_path,
    )

    return LlmRunResult(
        chosen_k=chosen_k,
        appliance_key=appliance_key,
        selected_cycle_id=selected_cycle_id,
        dominant_cluster=dominant_cluster,
        dominant_cluster_n_cycles=dominant_cluster_n_cycles,
        selected_cluster=selected_cluster,
        output_dir=output_dir,
        cases_eval_csv=cases_eval_csv,
        selected_case_csv=selected_case_csv,
    )


def open_llm_smartmeter_ui(ctx: AppContext):
    win = tk.Toplevel(ctx.window)
    win.title("LLM Smart Meter")
    win.geometry("760x430")

    frm = tk.Frame(win)
    frm.pack(fill="both", expand=True, padx=12, pady=12)

    tk.Label(frm, text="Appliance").grid(row=0, column=0, sticky="w")
    appliance_var = tk.StringVar(value="Computer")
    appliance_combo = ttk.Combobox(
        frm,
        textvariable=appliance_var,
        values=list(APPLIANCE_OPTIONS.keys()),
        state="readonly",
        width=30,
    )
    appliance_combo.grid(row=0, column=1, sticky="w", padx=(8, 0))

    tk.Label(frm, text="CSV source").grid(row=1, column=0, sticky="w", pady=(10, 0))
    csv_var = tk.StringVar()
    csv_entry = tk.Entry(frm, textvariable=csv_var, width=48)
    csv_entry.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(10, 0))

    def _browse_csv():
        path = filedialog.askopenfilename(
            title="Select CSV file",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if path:
            csv_var.set(path)

    tk.Button(frm, text="Browse", command=_browse_csv).grid(row=1, column=2, sticky="w", padx=(8, 0), pady=(10, 0))

    tk.Label(frm, text="Parameters").grid(row=2, column=0, sticky="w", pady=(12, 0))
    params_mode_var = tk.StringVar(value="default")

    params_wrap = tk.Frame(frm)
    params_wrap.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(12, 0))
    tk.Radiobutton(params_wrap, text="Use default parameters", value="default", variable=params_mode_var).pack(anchor="w")
    tk.Radiobutton(params_wrap, text="Use custom JSON parameters", value="custom", variable=params_mode_var).pack(anchor="w")

    params_path_var = tk.StringVar()
    params_entry = tk.Entry(frm, textvariable=params_path_var, width=48, state="disabled")
    params_entry.grid(row=3, column=1, sticky="w", padx=(8, 0), pady=(6, 0))

    def _browse_params_json():
        path = filedialog.askopenfilename(
            title="Select parameters JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            params_path_var.set(path)

    params_browse_btn = tk.Button(frm, text="Browse", command=_browse_params_json, state="disabled")
    params_browse_btn.grid(row=3, column=2, sticky="w", padx=(8, 0), pady=(6, 0))

    tk.Label(frm, text="K mode").grid(row=4, column=0, sticky="w", pady=(14, 0))
    k_mode_var = tk.StringVar(value="best")

    rb_wrap = tk.Frame(frm)
    rb_wrap.grid(row=4, column=1, sticky="w", padx=(8, 0), pady=(14, 0))

    tk.Radiobutton(rb_wrap, text="Best k (vote)", value="best", variable=k_mode_var).pack(anchor="w")
    tk.Radiobutton(rb_wrap, text="Human k", value="human", variable=k_mode_var).pack(anchor="w")
    tk.Radiobutton(rb_wrap, text="Custom k", value="custom", variable=k_mode_var).pack(anchor="w")

    custom_k_var = tk.StringVar(value="20")
    custom_k_entry = tk.Entry(frm, textvariable=custom_k_var, width=8, state="disabled")
    custom_k_entry.grid(row=4, column=2, sticky="w", padx=(8, 0), pady=(14, 0))

    def _on_params_mode_change(*_args):
        custom_mode = params_mode_var.get() == "custom"
        params_entry.config(state="normal" if custom_mode else "disabled")
        params_browse_btn.config(state="normal" if custom_mode else "disabled")

    params_mode_var.trace_add("write", _on_params_mode_change)

    def _on_k_mode_change(*_args):
        if k_mode_var.get() == "custom":
            custom_k_entry.config(state="normal")
        else:
            custom_k_entry.config(state="disabled")

    k_mode_var.trace_add("write", _on_k_mode_change)

    status_var = tk.StringVar(value="Ready")
    tk.Label(frm, textvariable=status_var, fg="blue").grid(row=5, column=0, columnspan=3, sticky="w", pady=(16, 0))

    btn_row = tk.Frame(frm)
    btn_row.grid(row=6, column=0, columnspan=3, sticky="e", pady=(20, 0))

    start_btn = tk.Button(btn_row, text="Avvia", width=12)
    start_btn.pack(side="right")

    def _set_running(running: bool):
        state = "disabled" if running else "normal"
        start_btn.config(state=state)
        appliance_combo.config(state="disabled" if running else "readonly")
        csv_entry.config(state=state)
        custom_k_entry.config(state="normal" if (not running and k_mode_var.get() == "custom") else "disabled")
        params_entry.config(state="normal" if (not running and params_mode_var.get() == "custom") else "disabled")
        params_browse_btn.config(state="normal" if (not running and params_mode_var.get() == "custom") else "disabled")

    def _run_worker(
        appliance_label: str,
        csv_source: str,
        k_mode: str,
        custom_k: int | None,
        custom_params_path: str | None,
    ):
        try:
            result = _run_llm_pipeline(
                appliance_label=appliance_label,
                csv_path=Path(csv_source),
                k_mode=k_mode,
                custom_k=custom_k,
                custom_params_path=custom_params_path,
            )

            def _done_ok():
                _set_running(False)
                status_var.set("Done")
                messagebox.showinfo(
                    "LLM Smart Meter",
                    "Completed.\n"
                    f"Appliance: {result.appliance_key}\n"
                    f"Chosen k: {result.chosen_k}\n"
                    f"Dominant cluster: {result.dominant_cluster} (n={result.dominant_cluster_n_cycles})\n"
                    f"Selected cluster: {result.selected_cluster}\n\n"
                    f"Selected cycle id: {result.selected_cycle_id}\n\n"
                    f"Cases CSV: {result.cases_eval_csv}\n"
                    f"Selected case CSV: {result.selected_case_csv}",
                )

            win.after(0, _done_ok)
        except Exception as e:
            logger.exception("LLM Smart Meter failed")
            err_msg = str(e)

            def _done_err():
                _set_running(False)
                status_var.set("Error")
                messagebox.showerror("LLM Smart Meter", f"Execution failed:\n{err_msg}")

            win.after(0, _done_err)

    def _start():
        appliance_label = appliance_var.get().strip()
        csv_source = csv_var.get().strip()
        k_mode = k_mode_var.get().strip()
        params_mode = params_mode_var.get().strip()

        if appliance_label not in APPLIANCE_OPTIONS:
            messagebox.showerror("LLM Smart Meter", "Select a valid appliance.")
            return

        if not csv_source:
            messagebox.showerror("LLM Smart Meter", "Select a CSV file.")
            return

        if not Path(csv_source).is_file():
            messagebox.showerror("LLM Smart Meter", "Selected CSV file does not exist.")
            return

        custom_params_path = None
        if params_mode == "custom":
            custom_params_path = params_path_var.get().strip()
            if not custom_params_path:
                messagebox.showerror("LLM Smart Meter", "Select a custom parameters JSON file.")
                return
            if not Path(custom_params_path).is_file():
                messagebox.showerror("LLM Smart Meter", "Selected parameters JSON file does not exist.")
                return

        custom_k = None
        if k_mode == "custom":
            raw = custom_k_var.get().strip()
            try:
                custom_k = int(raw)
            except Exception:
                messagebox.showerror("LLM Smart Meter", "Custom k must be an integer.")
                return
            if custom_k < 2:
                messagebox.showerror("LLM Smart Meter", "Custom k must be >= 2.")
                return

        _set_running(True)
        status_var.set("Running...")
        th = threading.Thread(
            target=_run_worker,
            args=(appliance_label, csv_source, k_mode, custom_k, custom_params_path),
            daemon=True,
        )
        th.start()

    start_btn.config(command=_start)
