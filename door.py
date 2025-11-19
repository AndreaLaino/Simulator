import tkinter as tk
from point import points
from read import coordinates, read_doors

doors = []

def draw_line_door(canvas, window, load_active):
    global doors
    if load_active:
        point = coordinates
    else:
        point = points

    def draw_line():
        point1 = point1_entry.get()
        point2 = point2_entry.get()

        # search for the coordinates of the points
        coord_point1 = None
        coord_point2 = None
        for p_point in point:
            if p_point[0] == point1:
                coord_point1 = (p_point[1], p_point[2])
            elif p_point[0] == point2:
                coord_point2 = (p_point[1], p_point[2])

        if coord_point1 and coord_point2:
            state = "close"
            door = (coord_point1[0], coord_point1[1], coord_point2[0], coord_point2[1], state)
            if load_active:
                read_doors.append(door)
            else:
                doors.append(door)
            draw_door(canvas, door)
            line_window.destroy()

    # window for points name
    line_window = tk.Toplevel(window)
    line_window.title("Add door")

    tk.Label(line_window, text="Door").pack()

    tk.Label(line_window, text="Point 1:").pack()
    point1_entry = tk.Entry(line_window)
    point1_entry.pack()

    tk.Label(line_window, text="Point 2:").pack()
    point2_entry = tk.Entry(line_window)
    point2_entry.pack()

    # draw line button
    tk.Button(line_window, text="Draw Line", command=draw_line).pack()


def draw_door(canvas, door):
    x1, y1, x2, y2, state = door
    if state == 'close':
        canvas.create_line(x1, y1, x2, y2, fill="green", width=4, tags="door")
    else:
        canvas.create_line(x1, y1, x2, y2, fill="grey", width=4, dash=(4, 2), tags="door")

def draw_all_doors(canvas, doors):
    canvas.delete("door")
    for door in doors:
        draw_door(canvas, door)



def interaction_with_door(canvas, event, doors):
    x = canvas.canvasx(event.x)
    y = canvas.canvasy(event.y)

    tolerance = 5  # defines the maximum click distance from the door that can be tolerated.

    for index, door in enumerate(doors):
        if len(door) == 5:  # check if door format is valid
            x1, y1, x2, y2, state = door
            if point_in_line(x, y, x1, y1, x2, y2, tolerance):
                print(f"Interaction with door {index} at coordinates ({x1}, {y1}), ({x2}, {y2}) with state {state}")

                # change the door state
                toggle_door_state(index, doors)
                draw_all_doors(canvas, doors)
                break
        else:
            print(f"Door format not valid: {door}")

def point_in_line(px, py, x1, y1, x2, y2, tolerance):
    # Check if a point (px, py) is near a line (x1, y1, x2, y2).
    line_mag = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    if line_mag < tolerance:
        return False
    u = ((px - x1) * (x2 - x1) + (py - y1) * (y2 - y1)) / (line_mag ** 2)
    if u < 0 or u > 1:
        return False
    ix = x1 + u * (x2 - x1)
    iy = y1 + u * (y2 - y1)
    dist = ((px - ix) ** 2 + (py - iy) ** 2) ** 0.5
    return dist < tolerance

def toggle_door_state(index, doors):
    if 0 <= index < len(doors):  # check if index is valid
        x1, y1, x2, y2, state = doors[index]
        new_state = 'open' if state == 'close' else 'close'
        print(f"Toggled door {index} state from {state} to {new_state}")
        doors[index] = (x1, y1, x2, y2, new_state)
    else:
        print(f"Door index not valid: {index}")