from utils import update_sensor_color

sensor_states = {}
active_cycles = {}

def update_sensor_states(name, state, sensor_states, timestamp):
    if name not in sensor_states:
        sensor_states[name] = {'time': [], 'state': []}
    sensor_states[name]['time'].append(timestamp)
    sensor_states[name]['state'].append(state)
    print(f"Sensor state updated: {name} -> {state}")

# this function is not in the sensor.py file to avoid cyclic import
def changeSwitch(canvas, sensor, sensors, door_state):
    if len(sensor) != 11:
        print(f"Error: unexpected switch sensor structure {sensor}")
        return None, None, sensors

    name, x, y, type, min_val, max_val, step, state, direction, consumption, associated_device = sensor

    try:
        numeric_state = float(door_state)
    except ValueError:
        if isinstance(door_state, str):
            if door_state.lower() == "open":
                numeric_state = 1.0
            elif door_state.lower() == "close":
                numeric_state = 0.0
            else:
                numeric_state = 0.0
        else:
            numeric_state = 0.0

    new_state = numeric_state

    updated_sensors = []
    for s in sensors:
        if s == sensor:
            updated_sensor = (name, x, y, type, min_val, max_val, step, new_state, direction, consumption, associated_device)
            updated_sensors.append(updated_sensor)
        else:
            updated_sensors.append(s)

    update_sensor_color(canvas, name, new_state, float(min_val))
    return name, new_state, updated_sensors
