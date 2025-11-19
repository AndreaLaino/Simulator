import tkinter as tk
from utils import draw_sensor
import csv

# Global lists to save data read from file
coordinates = []
read_walls = []
read_sensors = []
read_devices = []
read_doors = []

def read_coordinates_from_file(file_path):
    # Variable to track the current section
    current_section = None

    with open(file_path, "r") as csvfile:
        reader = csv.reader(csvfile)
        for row in reader:
            # Skip empty rows
            if not row:
                continue
            section_name = row[0].strip().lower()
            if section_name == "positions":
                current_section = "Positions"
                continue
            elif section_name == "walls":
                current_section = "Walls"
                continue
            elif section_name == "sensors":
                current_section = "Sensors"
                continue
            elif section_name == "devices":
                current_section = "Devices"
                continue
            elif section_name == "doors":
                current_section = "Doors"
                continue

            if current_section == "Positions":
                try:
                    name_p, x_p, y_p = row
                    x_p = int(x_p)
                    y_p = int(y_p)
                    coordinates.append((name_p, x_p, y_p))
                except ValueError:
                    print(f"Error in Positions row: {row}")

            elif current_section == "Walls":
                try:
                    point1, point2 = row
                    read_walls.append((point1, point2))
                except ValueError:
                    print(f"Error in Walls row: {row}")

            elif current_section == "Sensors":
                try:
                    if len(row) < 11:
                        print(f"Sensors row incomplete: {row}")
                        continue
                    (name_s, x_s, y_s, type, min_val, max_val, step, state_s,
                     direction, consumption, associated_device) = row
                    x_s = int(x_s)
                    y_s = int(y_s)
                    min_val = float(min_val)
                    max_val = float(max_val)
                    step = float(step)
                    state_s = float(state_s)
                    # the direction field: if "None" or empty, set None, otherwise convert to float
                    direction = None if direction in ("", "None") else float(direction)
                    # the consumption field: if "None"or empty, set None, otherwise converted to float
                    consumption = None if consumption in ("", "None") else float(consumption)
                    read_sensors.append((name_s, x_s, y_s, type, min_val, max_val, step, state_s, direction, consumption, associated_device))
                except ValueError:
                    print(f"Error in Sensors row: {row}")

            elif current_section == "Devices":
                try:
                    if len(row) < 10:
                        print(f"Devices row incomplete: {row}")
                        continue
                    (name_d, x_d, y_d, type_d, power, state_d, min_consumption,
                     max_consumption, current_consumption, consumption_direction) = row
                    x_d = int(x_d)
                    y_d = int(y_d)
                    power = int(float(power))
                    state_d = int(state_d)
                    min_consumption = int(float(min_consumption))
                    max_consumption = int(float(max_consumption))
                    current_consumption = int(float(current_consumption))
                    consumption_direction = int(consumption_direction)
                    read_devices.append((name_d, x_d, y_d, type_d, power, state_d, min_consumption, max_consumption, current_consumption, consumption_direction))
                except ValueError:
                    print(f"Error Devices row: {row}")

            elif current_section == "Doors":
                try:
                    x1_p, y1_p, x2_p, y2_p, state_p = row
                    x1_p = int(x1_p)
                    y1_p = int(y1_p)
                    x2_p = int(x2_p)
                    y2_p = int(y2_p)
                    read_doors.append((x1_p, y1_p, x2_p, y2_p, state_p))
                except ValueError:
                    print(f"Error in Doors row: {row}")
    return coordinates, read_walls, read_sensors, read_devices, read_doors

def draw_points(coordinates, canvas):
    for name, x, y in coordinates:
        canvas.create_oval(x - 5, y - 5, x + 5, y + 5, fill="blue", tags='point')
        canvas.create_text(x+7, y, text=name, fill="blue", anchor=tk.SW, tags='point')

read_walls_coordinates = []

def draw_walls(read_walls, coordinates, canvas):
    for point1, point2 in read_walls:
        coord_point1 = None
        coord_point2 = None
        for coord in coordinates:
            if coord[0] == point1:
                coord_point1 = (coord[1], coord[2])
            elif coord[0] == point2:
                coord_point2 = (coord[1], coord[2])
        if coord_point1 is not None and coord_point2 is not None:
            canvas.create_line(coord_point1, coord_point2, fill="red", width=3, tags='wall')
            read_walls_coordinates.extend([coord_point1[0], coord_point1[1], coord_point2[0], coord_point2[1]])
        else:
            print(f"Coordinates not found: {point1}, {point2}")

def draw_sensors(read_sensors, canvas):
    for sensor in read_sensors:
        draw_sensor(canvas, sensor)

def draw_devices(read_devices, canvas):
    for device in read_devices:
        name, x, y, type, power, state, min_consumption, max_consumption, current_consumption, consumption_direction = device
        color = "red" if state == 0 else "green"
        canvas.create_oval(x - 5, y - 5, x + 5, y + 5, fill=color, tags=(name, 'device'))
        canvas.create_text(x+7, y, text=f"{name} ({type})", fill=color, anchor=tk.SW, tags=(name, 'device'))

def draw_doors(read_doors, canvas):
    for x1, y1, x2, y2, state in read_doors:
        if state == 'close':
            canvas.create_line(x1, y1, x2, y2, fill="green", width=5, tags="door")
        else:
            canvas.create_line(x1, y1, x2, y2, fill="grey", width=3, dash=(4, 2), tags="door")
