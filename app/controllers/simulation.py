from __future__ import annotations
import tkinter as tk

from timer import TimerApp
from sim import start_simulation, stop_simulation, interaction, update_sensors
from activity import monitor_activities, close_current_activity
from log import start_interaction_log_session, stop_interaction_log_session
from app.context import AppContext
from app.logging_setup import setup_logging

logger = setup_logging("controllers.simulation")

def start_sim(ctx: AppContext):
    """Start manual simulation and wire callbacks."""
    from tkinter import messagebox
    from sensor import sensors
    from read import read_sensors as rs

    s_sensors = rs if ctx.load_active else sensors
    if not s_sensors:
        messagebox.showwarning("Error", "No sensors found to start the simulation.")
        return
    
    ctx.simulation_menu.entryconfig("Manual", state="disabled")

    timer_app_instance = TimerApp(
        ctx.timer_frame,
        start_callback=lambda: (
            start_simulation(ctx.canvas, timer_app_instance, ctx.load_active, ctx.activity_label),
            monitor_activities(ctx.canvas, ctx.load_active, ctx.activity_label, timer_app_instance),
            start_interaction_log_session(timer_app_instance.get_simulated_time()),
            ctx.canvas.bind("<Button-1>", lambda event: interaction(ctx.canvas, timer_app_instance, event, ctx.load_active, ctx.activity_label))
        ),
        stop_callback=lambda: (
            stop_simulation(timer_app_instance),
            close_current_activity(timer_app_instance, ctx.activity_label),
            stop_interaction_log_session(),
            ctx.canvas.unbind("<Button-1>")
        )
    )

    ctx.activity_label = tk.Label(ctx.timer_frame, text="Activity: None", font=("Helvetica", 16), bg="white", fg="black")
    ctx.activity_label.pack(pady=10)

    update_sensors(ctx.canvas, timer_app_instance, ctx.load_active, ctx.activity_label)
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