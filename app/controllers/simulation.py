from __future__ import annotations
import tkinter as tk

from timer import TimerApp
from sim import start_simulation, stop_simulation, interaction, update_sensors
from activity import monitor_activities, close_current_activity
from log import start_interaction_log_session, stop_interaction_log_session
from app.context import AppContext
from app.logging_setup import setup_logging

logger = setup_logging("controllers.simulation")


def _resolve_runtime_sources(load_active: bool) -> dict:
    from read import coordinates, read_sensors, read_walls_coordinates, read_devices, read_doors
    from point import points
    from sensor import sensors
    from wall import walls_coordinates
    from device import devices
    from door import doors

    if load_active:
        return {
            "points": coordinates,
            "sensors": read_sensors,
            "walls": read_walls_coordinates,
            "devices": read_devices,
            "doors": read_doors,
        }

    return {
        "points": points,
        "sensors": sensors,
        "walls": walls_coordinates,
        "devices": devices,
        "doors": doors,
    }


def _cleanup_manual_sim(ctx: AppContext):
    """Cleanup function called after stopping manual simulation."""
    setattr(ctx, 'timer_app_instance', None)
    if hasattr(ctx, 'activity_label') and ctx.activity_label is not None:
        try:
            ctx.activity_label.config(text="Activity: None")
        except:
            pass


def start_sim(ctx: AppContext):
    """Start manual simulation and wire callbacks."""
    from tkinter import messagebox
    runtime_sources = _resolve_runtime_sources(ctx.load_active)
    s_sensors = runtime_sources["sensors"]
    if not s_sensors:
        messagebox.showwarning("Error", "No sensors found to start the simulation.")
        return
    
    # Prevent multiple instances
    if hasattr(ctx, 'timer_app_instance') and ctx.timer_app_instance is not None:
        messagebox.showwarning("Warning", "Manual simulation is already running.")
        return

    sensor_states_store = ctx.house_state.sensor_states()
    
    # Clean up previous widgets if they exist
    if hasattr(ctx, 'activity_label') and ctx.activity_label is not None:
        try:
            ctx.activity_label.config(text="Activity: None")
        except:
            pass
    
    # Clean up all children of timer_frame from previous sessions
    if hasattr(ctx, 'timer_frame'):
        for widget in ctx.timer_frame.winfo_children():
            widget.destroy()

    ctx.simulation_menu.entryconfig("Manual", state="disabled")
    if hasattr(ctx, 'canvas') and ctx.canvas is not None:
        ctx.canvas.unbind("<Button-1>")

    def _bind_canvas_click():
        ctx.canvas.bind(
            "<Button-1>",
            lambda event: interaction(ctx.canvas, timer_app_instance, event, ctx.activity_label, ctx.house_state, runtime_sources),
        )

    timer_app_instance = TimerApp(
        ctx.timer_frame,
        start_callback=lambda: (
            _bind_canvas_click(),
            start_simulation(ctx.canvas, timer_app_instance, ctx.activity_label, ctx.house_state, runtime_sources),
            monitor_activities(ctx.canvas, ctx.activity_label, timer_app_instance, sensor_states_store, ctx.house_state, runtime_sources),
            start_interaction_log_session(ctx.house_state, timer_app_instance.get_simulated_time()),
        ),
        stop_callback=lambda: (
            stop_simulation(timer_app_instance),
            close_current_activity(timer_app_instance, ctx.activity_label, ctx.house_state),
            stop_interaction_log_session(ctx.house_state),
            ctx.canvas.unbind("<Button-1>") if hasattr(ctx, 'canvas') and ctx.canvas is not None else None,
            enable_all_menus(ctx),
            ctx.window.after(100, lambda: _cleanup_manual_sim(ctx)),
        ),
    )

    _bind_canvas_click()
    
    ctx.timer_app_instance = timer_app_instance

    if not hasattr(ctx, 'activity_label') or ctx.activity_label is None:
        ctx.activity_label = tk.Label(
            ctx.activity_frame, text="Activity: None", font=("Helvetica", 16), bg="white", fg="black"
        )
        ctx.activity_label.pack(pady=15, padx=10, fill=tk.BOTH, expand=True)
    
    timer_app_instance.on_advance_step = lambda ds: update_sensors(
        ctx.canvas,
        timer_app_instance,
        ctx.activity_label,
        ctx.house_state,
        runtime_sources,
        schedule_next=False,
        force=True,
        delta_override=ds,
        fast=True,
    )

    update_sensors(ctx.canvas, timer_app_instance, ctx.activity_label, ctx.house_state, runtime_sources)
    disable_all_menus(ctx)


def disable_all_menus(ctx: AppContext):
    for label in ["Add points", "Add sensors", "Add devices", "Add walls", "Add doors"]:
        ctx.scenario_menu.entryconfig(label, state="disabled")


def enable_all_menus(ctx: AppContext):
    for label in ["Add points", "Add sensors", "Add devices", "Add walls", "Add doors"]:
        ctx.scenario_menu.entryconfig(label, state="normal")


def exit_app(ctx: AppContext):
    from tkinter import messagebox

    if messagebox.askyesno("Exit", "Are you sure you want to close the application?"):
        try:
            if ctx.smart_logger is not None:
                ctx.smart_logger.stop()
                logger.info("[SmartMeter] logging stopped")
        except Exception as e:
            logger.warning("Stopping SmartMeterLogger failed: %s", e)
        ctx.window.quit()

__all__ = ["start_sim", "enable_all_menus", "exit_app"]
