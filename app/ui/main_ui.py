from __future__ import annotations

import tkinter as tk
from tkinter import Menu
from PIL import ImageTk, Image

from device import add_device
from door import draw_line_door
from point import add_point
from read import draw_points, draw_walls, draw_sensors, draw_devices, draw_doors
from sensor import add_sensor
from wall import draw_line_window
from graph import show_graphs
from automatic import launch_automatic_interface
from common import sensor_states

from app.context import AppContext
from app.controllers.simulation import start_sim, enable_all_menus, exit_app
from app.io.scenario import (
    load_scenario_from_file,
    open_scenario,
    save_scenario,
    save_scenario_as,
    delete_scenario,
    export_simulation_csv,
    import_csv,
    import_csv_from_s3,
)
from app.ui.bindings import open_bind_ip_ui, open_bind_dht_ui


def _load_image(canvas_obj: tk.Canvas, file_path: str):
    image = Image.open(file_path).resize((1200, 1200))
    photo = ImageTk.PhotoImage(image)
    canvas_obj.create_image(0, 0, anchor=tk.NW, image=photo, tags="background_image")
    canvas_obj.image = photo
    canvas_obj.config(scrollregion=canvas_obj.bbox(tk.ALL))


def _menu_add_point(ctx: AppContext):
    ctx.canvas.bind("<Button-1>", lambda event: add_point(ctx.canvas, event, ctx.load_active))
    ctx.scenario_menu.entryconfig("Add points", state="disabled")
    ctx.scenario_menu.entryconfig("Add sensors", state="normal")
    ctx.scenario_menu.entryconfig("Add devices", state="normal")


def _menu_add_device(ctx: AppContext):
    ctx.canvas.bind("<Button-1>", lambda event: add_device(ctx.canvas, event, ctx.load_active))
    ctx.scenario_menu.entryconfig("Add devices", state="disabled")
    ctx.scenario_menu.entryconfig("Add sensors", state="normal")
    ctx.scenario_menu.entryconfig("Add points", state="normal")


def _menu_add_sensor(ctx: AppContext):
    ctx.canvas.bind("<Button-1>", lambda event: add_sensor(ctx.canvas, event, ctx.load_active))
    ctx.scenario_menu.entryconfig("Add sensors", state="disabled")
    ctx.scenario_menu.entryconfig("Add devices", state="normal")
    ctx.scenario_menu.entryconfig("Add points", state="normal")


def _menu_add_wall(ctx: AppContext):
    draw_line_window(ctx.canvas, ctx.window, ctx.load_active)
    enable_all_menus(ctx)


def _menu_add_door(ctx: AppContext):
    draw_line_door(ctx.canvas, ctx.window, ctx.load_active)
    enable_all_menus(ctx)


def build_home_ui(ctx: AppContext):
    # If already built, just show it
    if getattr(ctx, "home_frame", None) is not None:
        ctx.home_frame.pack(fill=tk.BOTH, expand=True)
        return

    home_frame = tk.Frame(ctx.window)
    home_frame.pack(fill=tk.BOTH, expand=True)
    ctx.home_frame = home_frame

    # Left: canvas with scrollbars
    image_frame = tk.Frame(home_frame, width=900, height=900)
    image_frame.pack(side=tk.LEFT, padx=10, pady=10, fill=tk.BOTH, expand=True)

    h_scroll = tk.Scrollbar(image_frame, orient=tk.HORIZONTAL)
    h_scroll.pack(side=tk.BOTTOM, fill=tk.X)
    v_scroll = tk.Scrollbar(image_frame, orient=tk.VERTICAL)
    v_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    canvas = tk.Canvas(
        image_frame,
        width=900,
        height=900,
        xscrollcommand=h_scroll.set,
        yscrollcommand=v_scroll.set,
    )
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    h_scroll.config(command=canvas.xview)
    v_scroll.config(command=canvas.yview)
    ctx.canvas = canvas

    _load_image(canvas, "images/grid_25.PNG")

    # Right: timer panel
    timer_container = tk.Frame(home_frame, bg="lightgrey", width=400)
    timer_container.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
    timer_canvas = tk.Canvas(timer_container, bg="lightgrey")
    timer_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    timer_frame = tk.Frame(timer_canvas, bg="lightgrey", width=400)
    timer_canvas.create_window((0, 0), window=timer_frame, anchor="nw")
    ctx.timer_frame = timer_frame

    menu_bar = Menu(ctx.window)
    ctx.window.config(menu=menu_bar)

    # File menu
    file_menu = Menu(menu_bar, tearoff=0)
    ctx.file_menu = file_menu
    menu_bar.add_cascade(label="File", menu=file_menu)
    file_menu.add_command(label="New", command=lambda: delete_scenario(ctx, ctx.canvas))
    file_menu.add_command(label="Open file", command=lambda: open_scenario(ctx, ctx.canvas))
    file_menu.add_command(label="Load default (saved.csv)", command=lambda: load_scenario_from_file(ctx, ctx.canvas))
    file_menu.add_separator()
    file_menu.add_command(label="Save", command=lambda: save_scenario(ctx))
    file_menu.add_command(label="Save As", command=lambda: save_scenario_as(ctx))
    file_menu.add_separator()
    file_menu.add_command(label="Exit", command=lambda: exit_app(ctx))

    # Scenario menu
    scenario_menu = Menu(menu_bar, tearoff=0)
    menu_bar.add_cascade(label="Scenario", menu=scenario_menu)
    ctx.scenario_menu = scenario_menu

    scenario_menu.add_command(label="Add points", command=lambda: _menu_add_point(ctx))
    scenario_menu.add_command(label="Add devices", command=lambda: _menu_add_device(ctx))
    scenario_menu.add_command(label="Add walls", command=lambda: _menu_add_wall(ctx))
    scenario_menu.add_command(label="Add doors", command=lambda: _menu_add_door(ctx))
    scenario_menu.add_command(label="Add sensors", command=lambda: _menu_add_sensor(ctx))

    # Simulation menu
    sim_menu = Menu(menu_bar, tearoff=0)
    ctx.simulation_menu = sim_menu
    menu_bar.add_cascade(label="Simulation", menu=sim_menu)
    sim_menu.add_command(label="Automatic", command=lambda: launch_automatic_interface(ctx))
    sim_menu.add_command(label="Manual", command=lambda: start_sim(ctx))
    sim_menu.add_separator()
    sim_menu.add_command(label="Generate log", command=lambda: __import__("log").show_log(ctx.canvas, sensor_states, ctx.load_active))
    sim_menu.add_command(label="Activity Log", command=lambda: __import__("log").show_activity_log())
    sim_menu.add_command(label="Generate graphs", command=lambda: __import__("graph").show_graphs(ctx.canvas, sensor_states))
    sim_menu.add_separator()
    sim_menu.add_command(label="Import sensor CSV from S3", command=lambda: import_csv_from_s3(ctx.window))
    sim_menu.add_command(label="Import sensor CSV (local)", command=lambda: import_csv(ctx.window))
    sim_menu.add_command(label="Export simulations (CSV)", command=lambda: export_simulation_csv())
    sim_menu.add_separator()
    sim_menu.add_command(label="Bind Smart Meter (IP → sensor)", command=lambda: open_bind_ip_ui(ctx.window, sensor_states))
    sim_menu.add_command(label="Bind DHT22 (GPIO → sensor)", command=lambda: open_bind_dht_ui(ctx.window, sensor_states))



