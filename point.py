import tkinter as tk
from tkinter import simpledialog, messagebox
from read import coordinates

points = []
add_point_enabled = True


def add_point(canvas, event, load_active):
    global add_point_enabled
    if not add_point_enabled:
        return
    # Coordinate of the added point
    x = int(canvas.canvasx(event.x))  # Get the x coordinate relative to the canvas
    y = int(canvas.canvasy(event.y))  # Get the y coordinate relative to the canvas

    # Dialog window to set the point name
    point_name = simpledialog.askstring("Point name", "Insert point name:")

    if point_name is None:
        return

    if not point_name.strip():
        messagebox.showwarning("Input not valid", "Point name cannot be empty.")
        return
    # Prevent duplicate names (case-insensitive) across runtime and file points
    if point_name_exists(point_name):
        messagebox.showwarning("Input not valid", "Point name already exists.")
        return

    if load_active:
        coordinates.append((point_name, x, y))
    else:
        points.append((point_name, x, y))
    # Update canvas with the point
    canvas.create_oval(x - 5, y - 5, x + 5, y + 5, fill="blue", tags='point')
    canvas.create_text(x+7, y, text=point_name, fill="blue", anchor=tk.SW, tags='point')

def point_name_exists(name: str) -> bool:
    target = name.strip().lower()
    if not target:
        return False
    # Names in runtime list
    runtime_names = {p[0].strip().lower() for p in points}
    # Names in loaded-from-file list
    file_names = {p[0].strip().lower() for p in coordinates}
    return target in runtime_names or target in file_names
