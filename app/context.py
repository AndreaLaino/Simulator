from __future__ import annotations
from dataclasses import dataclass, field
import tkinter as tk
from typing import Optional, Tuple, List

@dataclass
class AppContext:
    """Shared application context/data."""
    window: tk.Tk
    canvas: Optional[tk.Canvas] = None
    timer_frame: Optional[tk.Frame] = None
    activity_label: Optional[tk.Label] = None
    
    file_menu: Optional[tk.Menu] = None
    scenario_menu: Optional[tk.Menu] = None
    simulation_menu: Optional[tk.Menu] = None

    # State variables
    load_active: bool = False
    current_file: Optional[str] = None
    
    r_points: list = field(default_factory=list)
    read_walls: list = field(default_factory=list)
    read_sensors: list = field(default_factory=list)
    read_devices: list = field(default_factory=list)
    read_doors: list = field(default_factory=list)

    # Optional reference if you need to stop a background logger on exit
    smart_logger: Optional[object] = None