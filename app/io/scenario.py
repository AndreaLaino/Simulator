from __future__ import annotations

import os, csv, shutil
from tkinter import messagebox, filedialog

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