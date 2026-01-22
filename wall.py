import tkinter as tk
from point import points
from read import read_walls_coordinates, coordinates, read_walls

walls = []
walls_coordinates = []

def draw_line_window(canvas, window, load_active):
    global walls

    # Pick the correct list of points depending on whether a scenario is loaded
    point_source = coordinates if load_active else points

    def draw_line():
        point1 = point1_entry.get()
        point2 = point2_entry.get()
        if load_active:
            read_walls.append((point1, point2))
            read_walls_coordinates.append(str(point1))
            read_walls_coordinates.append(str(point2))
        else:
            walls.append(str(point1))
            walls.append(str(point2))

        # Look up coordinates for the given point names
        coord_point1 = None
        coord_point2 = None
        for point in point_source:
            if point[0] == point1:
                coord_point1 = (point[1], point[2])
            elif point[0] == point2:
                coord_point2 = (point[1], point[2])

        # If both points exist, draw the line
        if coord_point1 and coord_point2:
            canvas.create_line(coord_point1, coord_point2, fill="red", width=3, tags='wall')
            walls_coordinates.append(coord_point1[0])
            walls_coordinates.append(coord_point1[1])
            walls_coordinates.append(coord_point2[0])
            walls_coordinates.append(coord_point2[1])
            window_line.destroy()

    # Dialog to input point names
    window_line = tk.Toplevel(window)
    window_line.title("Add wall")

    tk.Label(window_line, text="Wall").pack()


    # Label and input for first point
    tk.Label(window_line, text="Point 1:").pack()
    point1_entry = tk.Entry(window_line)
    point1_entry.pack()

    # Label and input for second point
    tk.Label(window_line, text="Point 2:").pack()
    point2_entry = tk.Entry(window_line)
    point2_entry.pack()

    # Button to draw the line
    tk.Button(window_line, text="Draw Line", command=draw_line).pack()
    return walls

