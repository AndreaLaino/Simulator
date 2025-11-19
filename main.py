from __future__ import annotations

import tkinter as tk
from app.context import AppContext
from app.logging_setup import setup_logging
from app.ui.main_ui import build_home_ui

def rebuild_main_interface(ctx: AppContext):
    win = ctx.window
    win.title("Simulator")
    build_home_ui(ctx)
    
def main():
    logger = setup_logging("app")
    logger.info("Starting Simulator application")

    window = tk.Tk()
    window.title("Simulator")

    ctx = AppContext(window=window)
    build_home_ui(ctx)

    window.mainloop()

if __name__ == "__main__":
    main()