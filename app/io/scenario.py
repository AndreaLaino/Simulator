from __future__ import annotations

import os, csv, shutil
from tkinter import messagebox, filedialog, simpledialog
import tkinter as tk
from tkinter import ttk
from point import points
from wall import walls
from sensor import sensors
from device import devices
from door import doors
from read import (
    read_coordinates_from_file, draw_points, draw_walls, draw_sensors, draw_devices, draw_doors
)
from app.context import AppContext
from app.logging_setup import setup_logging




logger = setup_logging("io.scenario")


def _load_scenario(ctx: AppContext, canvas, filename: str) -> None:
    """Load the scenario from 'filename' and draw it on the canvas."""
    (ctx.r_points,
     ctx.read_walls,
     ctx.read_sensors,
     ctx.read_devices,
     ctx.read_doors) = read_coordinates_from_file(filename)

    ctx.load_active = True
    ctx.current_file = filename

    draw_points(ctx.r_points, canvas)
    draw_walls(ctx.read_walls, ctx.r_points, canvas)
    draw_sensors(ctx.read_sensors, canvas)
    draw_devices(ctx.read_devices, canvas)
    draw_doors(ctx.read_doors, canvas)

    logger.info("Scenario loaded from %s", filename)


def load_scenario_from_file(ctx: AppContext, canvas) -> None:
    """Load the default save file: 'saved.csv'."""
    default_file = "saved.csv"
    if not os.path.exists(default_file):
        messagebox.showwarning("File not found", f"'{default_file}' not found.")
        return

    # Clear the current scenario (no prompt – this is a "quick load")
    _clear_scenario(ctx, canvas)
    _load_scenario(ctx, canvas, default_file)


def _write_scenario(ctx: AppContext, filename: str) -> None:
    """Write the current scenario to the given file."""
    if not messagebox.askyesno("Save", f"Do you want to save to:\n{filename}?"):
        return

    with open(filename, "w", newline='') as csvfile:
        csvwriter = csv.writer(csvfile, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)

        # Points
        csvwriter.writerow(["Positions"])
        data = points if not ctx.load_active else ctx.r_points
        for name, x, y in data:
            csvwriter.writerow([name, x, y])

        # Walls
        csvwriter.writerow([])
        csvwriter.writerow(["Walls"])
        if not ctx.load_active:
            for i in range(0, len(walls), 2):
                if i + 1 < len(walls):
                    csvwriter.writerow([walls[i], walls[i + 1]])
        else:
            for p1, p2 in ctx.read_walls:
                csvwriter.writerow([p1, p2])

        # Sensors
        csvwriter.writerow([])
        csvwriter.writerow(["Sensors"])
        src = sensors if not ctx.load_active else ctx.read_sensors
        for (name, x, y, typ, min_val, max_val, step, state,
             direction, consumption, associated_device) in src:
            min_val = float(min_val)
            max_val = float(max_val)
            step = float(step)
            state = float(state)
            consumption = float(consumption) if consumption is not None else "None"

            csvwriter.writerow([
                name, x, y, typ, min_val, max_val, step, state,
                direction if direction is not None else "None",
                consumption, associated_device
            ])

        # Devices
        csvwriter.writerow([])
        csvwriter.writerow(["Devices"])
        srcd = devices if not ctx.load_active else ctx.read_devices
        for (name, x, y, typ, power, state, min_c, max_c, curr_c, c_dir) in srcd:
            csvwriter.writerow([
                name, int(x), int(y), typ, float(power), int(state),
                float(min_c), float(max_c), float(curr_c),
                c_dir if c_dir is not None else "None"
            ])

        # Doors
        csvwriter.writerow([])
        csvwriter.writerow(["Doors"])
        srcdoors = doors if not ctx.load_active else ctx.read_doors
        for x1, y1, x2, y2, state in srcdoors:
            csvwriter.writerow([x1, y1, x2, y2, state])

    logger.info("Scenario saved successfully to %s.", filename)
    messagebox.showinfo("Saved", f"Scenario saved successfully to:\n{filename}")


def _clear_scenario(ctx: AppContext, canvas) -> None:
    """Internal helper: clear the scenario from canvas and memory, no confirmation."""
    for tag in ['point', 'wall', 'sensor', 'line', 'device', 'door', 'fov']:
        canvas.delete(tag)

    for lst in [points, walls, sensors, devices, doors,
                ctx.r_points, ctx.read_walls, ctx.read_sensors, ctx.read_devices, ctx.read_doors]:
        lst.clear()

    ctx.load_active = False
    ctx.current_file = None

    logger.info("Scenario cleared from canvas and memory.")


def delete_scenario(ctx: AppContext, canvas) -> None:
    """Clear the current scenario from canvas and memory, with confirmation."""
    if not messagebox.askyesno(
        "Delete",
        "Are you sure you want to delete the current scenario?\nAll unsaved changes will be lost."
    ):
        return

    _clear_scenario(ctx, canvas)


def open_scenario(ctx: AppContext, canvas) -> None:
    """Open... – let the user choose the scenario file to open."""
    filename = filedialog.askopenfilename(
        title="Open scenario",
        defaultextension=".csv",
        filetypes=[("Scenario files", "*.csv"), ("All files", "*.*")]
    )
    if not filename:
        return  # user cancelled

    # Clear current scenario silently, then load the new one
    _clear_scenario(ctx, canvas)
    _load_scenario(ctx, canvas, filename)



def _sanitize(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in (name or "").strip())

def _unique_path(path: str) -> str:
    """Se esiste già, aggiunge _2, _3... prima dell'estensione."""
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    i = 2
    while True:
        p2 = f"{root}_{i}{ext}"
        if not os.path.exists(p2):
            return p2
        i += 1

def _ask_choice(parent, title: str, label: str, choices: list[str], default: str | None = None) -> str | None:
    win = tk.Toplevel(parent) if parent else tk.Toplevel()
    win.title(title)
    win.resizable(False, False)
    if parent:
        win.transient(parent)
    win.grab_set()

    tk.Label(win, text=label).grid(row=0, column=0, padx=10, pady=(10, 5), sticky="w")

    var = tk.StringVar(value=default or (choices[0] if choices else ""))
    cb = ttk.Combobox(win, textvariable=var, values=choices, state="readonly", width=25)
    cb.grid(row=1, column=0, padx=10, pady=5)
    if choices:
        cb.current(choices.index(default) if default in choices else 0)

    out = {"value": None}

    def ok():
        out["value"] = var.get()
        win.destroy()

    def cancel():
        out["value"] = None
        win.destroy()

    btns = tk.Frame(win)
    btns.grid(row=2, column=0, padx=10, pady=(5, 10), sticky="e")
    tk.Button(btns, text="OK", command=ok).pack(side="left", padx=5)
    tk.Button(btns, text="Annulla", command=cancel).pack(side="left")

    win.wait_window()
    return out["value"]


def import_csv(parent=None) -> None:
    files = filedialog.askopenfilenames(
        title="Import CSV files",
        filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
    )
    if not files:
        return

    # 1) enum of sensor types
    sim_type = _ask_choice(
        parent,
        title="Tipo sensore",
        label="Seleziona il tipo di sensore:",
        choices=["PIR", "Temperature", "Switch", "Smart Meter", "Weight"],
        default="PIR",
    )
    if not sim_type:
        return

    # 2) mapping type → prefix (temperature -> dht, etc)
    prefix_by_type = {
        "PIR": "pir",
        "Temperature": "dht",
        "Smart Meter": "smartmeter",
        "Switch": "switch",
        "Weight": "weight",
    }
    prefix = prefix_by_type[sim_type]

    # 3) name
    base_name = simpledialog.askstring(
        "Nome",
        "Inserisci il nome (es: t1, t2, sm_pc, ...)",
        parent=parent
    )
    if not base_name:
        return
    base_name = _sanitize(base_name)

    logs_dir = "logs"
    os.makedirs(logs_dir, exist_ok=True)

    imported = 0
    for src in files:
        dest_name = f"{prefix}_{base_name}.csv"
        dest_path = _unique_path(os.path.join(logs_dir, dest_name))
        try:
            shutil.copy2(src, dest_path)
            logger.info("Imported %s -> %s", src, dest_path)
            imported += 1
        except Exception as e:
            logger.exception("Failed to import %s: %s", src, e)

    messagebox.showinfo("Import", f"Importati {imported} file in {logs_dir}/")
def export_simulation_csv() -> None:
    """Export the latest 'interactions.csv' from logs to a chosen location."""
    logs_root = "logs"
    if not os.path.isdir(logs_root):
        messagebox.showwarning("No logs", "Folder 'logs' not found.\nStart a manual simulation before exporting.")
        return

    candidates = []
    for name in os.listdir(logs_root):
        folder = os.path.join(logs_root, name)
        if os.path.isdir(folder):
            csv_path = os.path.join(folder, "interactions.csv")
            if os.path.isfile(csv_path):
                candidates.append((os.path.getmtime(csv_path), csv_path))

    if not candidates:
        messagebox.showwarning("No file", "'interactions.csv' not found.\nStart a manual simulation and retry.")
        return

    candidates.sort(reverse=True)
    src_csv = candidates[0][1]

    dest = filedialog.asksaveasfilename(
        title="Export simulation (CSV)",
        defaultextension=".csv",
        initialfile="simulation_interactions.csv",
        filetypes=[("CSV", "*.csv"), ("All files", "*.*")]
    )
    if not dest:
        return

    try:
        shutil.copyfile(src_csv, dest)
        messagebox.showinfo("Exported", f"File exported to:\n{dest}")
        logger.info("Exported interactions CSV to %s", dest)
    except Exception as e:
        logger.exception("Export failed")
        messagebox.showerror("Error", f"Unable to export the file:\n{e}")


def save_scenario_as(ctx: AppContext) -> None:
    """Save As... – always ask for a new file path."""
    filename = filedialog.asksaveasfilename(
        title="Save scenario as",
        defaultextension=".csv",
        initialfile="saved.csv",
        filetypes=[("Scenario files", "*.csv"), ("All files", "*.*")]
    )
    if not filename:
        return

    ctx.current_file = filename
    _write_scenario(ctx, filename)


def save_scenario(ctx: AppContext) -> None:
    """Save – if a file is already open, overwrite it; otherwise behave like Save As."""
    if not ctx.current_file:
        save_scenario_as(ctx)
    else:
        _write_scenario(ctx, ctx.current_file)