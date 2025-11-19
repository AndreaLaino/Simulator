import tkinter as tk
from tkinter import simpledialog, messagebox
from tkinter import ttk
from read import read_devices as device_file

devices = []


def get_device_params(device_type):
    params = {
        "Fridge": {"power": 150, "min_consumption": 50,  "max_consumption": 150},
        "Washing_Machine":   {"power": 500, "min_consumption": 300, "max_consumption": 500},
        "Oven":       {"power": 2000, "min_consumption": 1500, "max_consumption": 2000},
        "Coffee_Machine": {"power": 1000, "min_consumption": 100,  "max_consumption": 1300},
        "Computer":    {"power": 250, "min_consumption": 100, "max_consumption": 250},
        "Dishwasher": {"power": 1800, "min_consumption": 1000, "max_consumption": 1600}
    }
    return params.get(device_type, {"power": 100, "min_consumption": 50, "max_consumption": 100})

class DeviceDialog(simpledialog.Dialog):
    def body(self, master):
        tk.Label(master, text="Device name:").grid(row=0)
        tk.Label(master, text="Device type:").grid(row=1)
        self.device_name = tk.Entry(master)
        self.device_name.grid(row=0, column=1)
        self.device_type = ttk.Combobox(master, values=[
            "Fridge", "Washing_Machine", "Oven", "Coffee_Machine", "Computer", "Dishwasher"])
        self.device_type.grid(row=1, column=1)
        self.device_type.current(0)
        return self.device_name

    def validate(self):
        name = self.device_name.get().strip()
        if not name:
            messagebox.showwarning("Input not valid", "Device name cannot be empty.")
            return False
        # avoid duplicates by considering both runtimes and file uploads
        for d in devices + device_file:
            if name == d[0]:
                messagebox.showwarning("Input not valid", "Device name already present.")
                return False
        return True

    def apply(self):
        name = self.device_name.get()
        type = self.device_type.get()
        params = get_device_params(type)
        power = params["power"]
        min_consumption = params["min_consumption"]
        max_consumption = params["max_consumption"]
        self.result = (name, type, power, min_consumption, max_consumption)

def add_device(canvas, event, load_active):
    x = int(canvas.canvasx(event.x))
    y = int(canvas.canvasy(event.y))
    dialog = DeviceDialog(canvas.master, "Add device")
    if dialog.result:
        name, type, power, min_consumption, max_consumption = dialog.result
        device = (name, x, y, type, power, 0, min_consumption, max_consumption, 0, 1)

        if load_active:
            device_file.append(device)
        else:
            devices.append(device)

        draw_device(canvas, device)

def draw_device(canvas, device):
    name, x, y, type, power, state, *_ = device
    color = "red" if state == 0 else "green"
    canvas.create_oval(x - 5, y - 5, x + 5, y + 5, fill=color, tags=(name, 'device'))
    canvas.create_text(x+7, y, text=f"{name} ({type})", fill=color, anchor=tk.SW, tags=(name, 'device'))
