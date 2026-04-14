from utils import update_sensor_color
import logging

logger = logging.getLogger("common")

sensor_states = {}
active_cycles = {}

def update_sensor_states(name, state, sensor_states, timestamp):
    if name not in sensor_states:
        sensor_states[name] = {'time': [], 'state': []}
    sensor_states[name]['time'].append(timestamp)
    sensor_states[name]['state'].append(state)
    logger.debug(f"Sensor state updated: {name} -> {state}")

# this function is not in the sensor.py file to avoid cyclic import
def changeSwitch(canvas, sensor, sensors, door_state):
    name, x, y, type, min_val = sensor.name, sensor.x, sensor.y, sensor.type, sensor.min_val

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

    sensor.state = new_state

    update_sensor_color(canvas, name, new_state, float(min_val))
    return name, new_state, sensors
