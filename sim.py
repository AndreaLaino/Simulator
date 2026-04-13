#sim.py
from PIL import Image, ImageTk
from datetime import datetime, timedelta
from wall import walls_coordinates
from point import points
from door import doors, interaction_with_door
from device import devices
from read import coordinates, read_sensors, read_walls_coordinates, read_devices, read_doors
from sensor import sensors, changePIR, changeTemperature, changeSmartMeter, ChangeWeight
from utils import find_closest_sensor_within_fov, update_devices_consumption, find_closest_sensor_without_intersection, find_switch_sensors_by_doors, calculate_distance
from common import update_sensor_states, sensor_states, changeSwitch, active_cycles
from log import log_move, log_sensor_event, log_device_event, log_door_event
from app.context import AppContext
from models import Sensor, Device, Point, Door, Wall

last_temp_elapsed = None
avatar_image = None
avatar_id = None
sen_sim = []
MAX_DISTANCE = 230
FOV_ANGLE = 60
active_pir_sensors = []

PER_SECOND_SENSOR_SAMPLING = True
PER_SECOND_SENSOR_TYPES = {"PIR", "Switch", "Weight"}


def append_unique_binary(buffer, ts, state_val, type=None):
    """ Binary state (0/1) with dedup on timestamp:
    - if ts equals and same value -> overwrite (no unnecessary duplicates)
    - if ts equal but value different - > append (preserve edge 0↔1)"""
    s = 1 if int(round(float(state_val))) else 0

    if 'type' not in buffer and type:
        buffer['type'] = type
    buffer.setdefault('time', [])
    buffer.setdefault('state', [])

    if buffer['time'] and buffer['time'][-1] == ts:
        if buffer['state'][-1] == s:
            buffer['state'][-1] = s
        else:
            buffer['time'].append(ts)
            buffer['state'].append(s)
    else:
        buffer['time'].append(ts)
        buffer['state'].append(s)


def initialize_avatar_image():
    global avatar_image
    image = Image.open("images/omino.png")
    image = image.resize((20, 27))
    avatar_image = ImageTk.PhotoImage(image)

def start_simulation(canvas, timer_app_instance, load_active, activity_label):
    initialize_avatar_image()
    if not timer_app_instance.is_running:
        timer_app_instance.start_stop()
        timer_app_instance.elapsed_time = timedelta()
    else:
        print("Simulation started.")
        update_sensors(canvas, timer_app_instance, load_active, activity_label)

def stop_simulation(timer_app_instance):
    if timer_app_instance.is_running:
        timer_app_instance.start_stop()
        timer_app_instance.elapsed_time = timedelta()
    else:
        print("Simulation stopped.")

def get_simulation_datetime(timer_app_instance):
    simulated_time = timer_app_instance.get_simulated_time()
    current_date = timer_app_instance.current_date
    return datetime.strptime(f"{current_date} {simulated_time}", "%Y-%m-%d %H:%M")

# Handle user click: move avatar, pick closest PIR in FOV, fallback actions, and logging.
def interaction(canvas, timer_app_instance, event, load_active, activity_label):
    global avatar_id, active_pir_sensors

    x = canvas.canvasx(event.x)
    y = canvas.canvasy(event.y)

    if avatar_image is None:
        initialize_avatar_image()

    # Keep avatar visible even when the simulation is paused/stopped.
    if avatar_id is not None:
        canvas.delete(avatar_id)
    avatar_id = canvas.create_image(x, y, image=avatar_image)

    if not timer_app_instance.is_running:
        print("Error: Simulation not started. Press 'Start Simulation' before interact.")
        return

    simulated_time = timer_app_instance.get_simulated_time()
    current_date = timer_app_instance.current_date
    timestamp = f"{current_date} {simulated_time}"
    print(f"Time of pressure: {simulated_time} - Date: {current_date}")

    log_move(timestamp, int(x), int(y))

    # Choose which structures to use based on whether we have loaded the scenario or not
    if load_active:
        p_points = coordinates
        s_sensors = read_sensors
        walls = read_walls_coordinates
        d_devices = read_devices
        d_doors = read_doors
    else:
        p_points = points
        s_sensors = sensors
        walls = walls_coordinates
        d_devices = devices
        d_doors = doors

    if not s_sensors:
        print("No sensors exists.")
        return

    # PIR: Find the closest one in the FOV first and without walls/blocks
    closest_sensor_pir = find_closest_sensor_within_fov((x, y), s_sensors, walls, d_doors, MAX_DISTANCE, FOV_ANGLE)

    # turn off previous active PIRs, but NOT the one you are about to activate
    if closest_sensor_pir:
        for sensor in active_pir_sensors:
            # Handle both Sensor objects and tuples
            sensor_name_curr = sensor.name if isinstance(sensor, Sensor) else sensor[0]
            closest_name = closest_sensor_pir.name if isinstance(closest_sensor_pir, Sensor) else closest_sensor_pir[0]
            if sensor_name_curr == closest_name:
                continue
            name, state, s_sensors = changePIR(canvas, sensor, s_sensors, 0)
            if name not in sensor_states:
                sensor_states[name] = {'time': [], 'state': [], 'type': 'PIR'}
            append_unique_binary(sensor_states[name], timestamp, state, 'PIR')
            try:
                sx = int(sensor.x) if isinstance(sensor, Sensor) else int(sensor[1])
                sy = int(sensor.y) if isinstance(sensor, Sensor) else int(sensor[2])
            except Exception:
                sx, sy = 0, 0
            log_sensor_event(timestamp, name, "PIR", sx, sy, 0, "auto-off-prev")  # [LOG]

    active_pir_sensors = []

    if closest_sensor_pir:
        # force ON (1) without toggle to avoid 0->1 in the same minute
        name, state, s_sensors = changePIR(canvas, closest_sensor_pir, s_sensors, 1)
        sen_sim.append((name, state, timestamp))
        if name not in sensor_states:
            sensor_states[name] = {'time': [], 'state': [], 'type': 'PIR'}
        append_unique_binary(sensor_states[name], timestamp, state, 'PIR')
        active_pir_sensors.append(closest_sensor_pir)
        try:
            sx = int(closest_sensor_pir.x) if isinstance(closest_sensor_pir, Sensor) else int(closest_sensor_pir[1])
            sy = int(closest_sensor_pir.y) if isinstance(closest_sensor_pir, Sensor) else int(closest_sensor_pir[2])
        except Exception:
            sx, sy = 0, 0
        log_sensor_event(timestamp, name, "PIR", sx, sy, 1, "closest_in_fov")  # [LOG]

        # Activate device if the user clicks on it with a few tolerance pixels
        for device in d_devices:
            if isinstance(device, Device):
                dx, dy = device.x, device.y
            else:
                dev_name, dx, dy, type, power, dev_state, min_c, max_c, current_cons, cons_dir = device
            if abs(dx - x) <= 5 and abs(dy - y) <= 5:
                toggle_device_state(canvas, event, sensor_states, load_active, timer_app_instance, x, y)
                break
    else:
        # Temperature: If no valid PIR, look for the nearest sensor without obstacles
        closest_temperature_sensor = find_closest_sensor_without_intersection((x, y), s_sensors, walls)
        if closest_temperature_sensor:
            toggle_device_state(canvas, event, sensor_states, load_active, timer_app_instance)

    # Weight: activate sensor if clicked close (within 10 px) otherwise turn off
    for sensor in s_sensors:
        sensor_type = sensor.type if isinstance(sensor, Sensor) else sensor[3]
        if sensor_type == "Weight":
            sx = sensor.x if isinstance(sensor, Sensor) else sensor[1]
            sy = sensor.y if isinstance(sensor, Sensor) else sensor[2]
            distance = calculate_distance(x, y, sx, sy)
            if distance < 10:
                name, state, s_sensors = ChangeWeight(canvas, sensor, s_sensors, 1)
                if name not in sensor_states:
                    sensor_states[name] = {'time': [], 'state': [], 'type': 'Weight'}
                append_unique_binary(sensor_states[name], timestamp, state, 'Weight')
                log_sensor_event(timestamp, name, "Weight", int(sx), int(sy), 1, "click_nearby")  # [LOG]
            else:
                name, state, s_sensors = ChangeWeight(canvas, sensor, s_sensors, 0)
                if name not in sensor_states:
                    sensor_states[name] = {'time': [], 'state': [], 'type': 'Weight'}
                append_unique_binary(sensor_states[name], timestamp, state, 'Weight')
                log_sensor_event(timestamp, name, "Weight", int(sx), int(sy), 0, "auto_off")  # [LOG]

    # Doors + Switch
    interaction_with_door(canvas, event, d_doors)

    switches_by_door = find_switch_sensors_by_doors(d_doors, s_sensors)
    for door, associated_sensors, door_state in switches_by_door:
        for sensor in associated_sensors:
            sw_name, sw_state, s_sensors = changeSwitch(canvas, sensor, s_sensors, door_state)
            if sw_name not in sensor_states:
                sensor_states[sw_name] = {'time': [], 'state': [], 'type': 'Switch'}
            append_unique_binary(sensor_states[sw_name], timestamp, int(sw_state), 'Switch')
            try:
                sx = int(sensor.x) if isinstance(sensor, Sensor) else int(sensor[1])
                sy = int(sensor.y) if isinstance(sensor, Sensor) else int(sensor[2])
            except Exception:
                sx, sy = 0, 0
            door_name = door.x1 if isinstance(door, Door) else door[0]
            log_sensor_event(timestamp, sw_name, "Switch", sx, sy, int(sw_state), f"sync_with_door:{door_name}")  # [LOG]
            print(f"Interact with switch sensor: {sw_name}, State: {sw_state}, Door: {door_name}, Time: {simulated_time} - Date: {current_date}")

    # Save updates (if necessary)
    if load_active:
        read_sensors[:] = s_sensors
        read_devices[:] = d_devices
    else:
        sensors[:] = s_sensors
        d_devices[:] = d_devices

def toggle_device_state(canvas, event, sensor_states, load_active, timer_app_instance, x=None, y=None):
    """toggle device state on click and log the event"""
    if x is None or y is None:
        x = int(canvas.canvasx(event.x))
        y = int(canvas.canvasy(event.y))

    if load_active:
        s_sensors = read_sensors
        d_devices = read_devices
        walls = read_walls_coordinates
        d_doors = read_doors
    else:
        s_sensors = sensors
        d_devices = devices
        walls = walls_coordinates
        d_doors = doors

    simulated_time = timer_app_instance.get_simulated_time()
    current_date = timer_app_instance.current_date
    current_timestamp = f"{current_date} {simulated_time}"
    simulation_datetime = get_simulation_datetime(timer_app_instance)
    OVEN_DISTANCE_THRESHOLD = 50

    for i, device in enumerate(d_devices):
        # Handle both Device objects and tuples
        if isinstance(device, Device):
            dev_name, dx, dy, type_d, power, dev_state, min_c, max_c = device.name, device.x, device.y, device.type, device.power, device.state, device.min_consumption, device.max_consumption
            current_cons, cons_dir = device.current_consumption, device.consumption_direction
        else:
            dev_name, dx, dy, type_d, power, dev_state, min_c, max_c, current_cons, cons_dir = device
        
        if abs(dx - x) <= 5 and abs(dy - y) <= 5:
            new_state = 0 if dev_state == 1 else 1

            if new_state == 1:
                current_cons = min_c
                cons_dir = 1
                active_cycles[dev_name] = (simulation_datetime, type_d)
            else:
                if type_d != "Fridge" and dev_name in active_cycles:
                    del active_cycles[dev_name]
                # Do not change current_cons for Fridge: continue the descent

            # Update device
            if isinstance(device, Device):
                device.state = new_state
                device.current_consumption = current_cons
                device.consumption_direction = cons_dir
                d_devices[i] = device
            else:
                d_devices[i] = (dev_name, dx, dy, type_d, power, new_state, min_c, max_c, current_cons, cons_dir)
            
            canvas.itemconfig(dev_name, fill="red" if new_state == 0 else "green")

            # toggle device
            log_device_event(current_timestamp, dev_name, type_d, int(dx), int(dy), int(new_state), "user_toggle_at_click")  # [LOG]

            for sensor in s_sensors:
                sensor_type = sensor.type if isinstance(sensor, Sensor) else sensor[3]
                if sensor_type == "Temperature":
                    oven_active = False
                    sensor_x = sensor.x if isinstance(sensor, Sensor) else sensor[1]
                    sensor_y = sensor.y if isinstance(sensor, Sensor) else sensor[2]
                    for dev in d_devices:
                        dev_type = dev.type if isinstance(dev, Device) else dev[3]
                        dev_state = dev.state if isinstance(dev, Device) else dev[5]
                        if dev_type == "Oven" and dev_state == 1:
                            device_x = dev.x if isinstance(dev, Device) else dev[1]
                            device_y = dev.y if isinstance(dev, Device) else dev[2]
                            distance = ((sensor_x - device_x) ** 2 + (sensor_y - device_y) ** 2) ** 0.5
                            if distance <= OVEN_DISTANCE_THRESHOLD:
                                oven_active = True
                                break
                    sensor_name, sensor_state, s_sensors = changeTemperature(canvas, sensor, s_sensors, 1 if oven_active else 0, 1.0, simulation_datetime, d_devices)
                    update_sensor_states(sensor_name, sensor_state, sensor_states, current_timestamp)
            break

def update_sensors(canvas, timer_app_instance, load_active, activity_label, *, schedule_next=True, force=False, delta_override=None, fast=False):
    """update sensors based on elapsed time and log events"""
    global sensors, read_sensors, last_temp_elapsed

    if (not timer_app_instance.is_running) and (not force):
        print("Error: Simulation not started.")
        return

    ui_canvas = None if fast else canvas

    if load_active:
        s_sensors = read_sensors
        d_devices = read_devices
        walls = read_walls_coordinates
        d_doors = read_doors
    else:
        s_sensors = sensors
        d_devices = devices
        walls = walls_coordinates
        d_doors = doors

    current_elapsed = timer_app_instance.elapsed_time
    if last_temp_elapsed is None:
        last_temp_elapsed = current_elapsed

    if delta_override is not None:
        delta_seconds = float(delta_override)
        last_temp_elapsed = current_elapsed
    else:
        delta_seconds = (current_elapsed - last_temp_elapsed).total_seconds()
        last_temp_elapsed = current_elapsed

        if delta_seconds <= 0:
            delta_seconds = 1.0

    simulated_time = timer_app_instance.get_simulated_time()
    current_date = timer_app_instance.current_date
    timestamp = f"{current_date} {simulated_time}"
    OVEN_DISTANCE_THRESHOLD = 50

    current_datetime = get_simulation_datetime(timer_app_instance)

    # Dedup helper for smartmeter
    def _append_unique_sample(buffer, ts, state_val, consumption_val=None):
        def _to_float(v):
            try:
                return float(round(float(v), 2))
            except Exception:
                return float("nan")

        state_float = _to_float(state_val)
        buffer.setdefault('time', [])
        buffer.setdefault('state', [])
        if 'consumption' in buffer:
            buffer.setdefault('consumption', [])

        if buffer['time'] and buffer['time'][-1] == ts:
            buffer['state'][-1] = state_float
            if 'consumption' in buffer and consumption_val is not None:
                buffer['consumption'][-1] = _to_float(consumption_val)
        else:
            buffer['time'].append(ts)
            buffer['state'].append(state_float)
            if 'consumption' in buffer and consumption_val is not None:
                buffer['consumption'].append(_to_float(consumption_val))

    # --- Temperature ---
    for i in range(len(s_sensors)):
        sensor = s_sensors[i]
        sensor_type = sensor.type if isinstance(sensor, Sensor) else sensor[3]
        if sensor_type == "Temperature":
            sensor_x = sensor.x if isinstance(sensor, Sensor) else sensor[1]
            sensor_y = sensor.y if isinstance(sensor, Sensor) else sensor[2]

            oven_active = False
            for device in d_devices:
                dev_type = device.type if isinstance(device, Device) else device[3]
                dev_state = device.state if isinstance(device, Device) else device[5]
                if dev_type == "Oven" and dev_state == 1:
                    device_x = device.x if isinstance(device, Device) else device[1]
                    device_y = device.y if isinstance(device, Device) else device[2]
                    dist = calculate_distance(sensor_x, sensor_y, device_x, device_y)
                    if dist <= OVEN_DISTANCE_THRESHOLD:
                        oven_active = True
                        break

            heating_factor = 1 if oven_active else 0
            sensor_name, new_state, s_sensors = changeTemperature(
                ui_canvas, sensor, s_sensors, heating_factor, delta_seconds, current_datetime, d_devices
            )
            update_sensor_states(sensor_name, new_state, sensor_states, timestamp)

    # --- Smart Meter ---
    updated_smartmeters = set()
    for i in range(len(s_sensors)):
        sensor = s_sensors[i]
        sensor_type = sensor.type if isinstance(sensor, Sensor) else sensor[3]
        if sensor_type == "Smart Meter":
            sensor_name, new_consumption, s_sensors = changeSmartMeter(
                ui_canvas, sensor, s_sensors, d_devices, delta_seconds, current_datetime
            )

            sensor_type_name = "Smart Meter"
            associated_device = sensor.associated_device if isinstance(sensor, Sensor) else sensor[10]

            if sensor_name not in sensor_states:
                sensor_states[sensor_name] = {
                    'time': [],
                    'state': [],
                    'consumption': [],
                    'type': sensor_type_name,
                    'associated_device': associated_device
                }
            else:
                sensor_states[sensor_name].setdefault('type', sensor_type_name)
                sensor_states[sensor_name].setdefault('associated_device', associated_device)
                sensor_states[sensor_name].setdefault('consumption', [])

            THRESHOLD_W = 1.0
            bin_state = 1 if (new_consumption or 0.0) > THRESHOLD_W else 0

            sensor_x = sensor.x if isinstance(sensor, Sensor) else sensor[1]
            sensor_y = sensor.y if isinstance(sensor, Sensor) else sensor[2]

            _append_unique_sample(
                sensor_states[sensor_name],
                timestamp,
                bin_state,
                round(new_consumption, 2)
            )

            log_sensor_event(
                timestamp,
                sensor_name,
                "Smart Meter",
                int(sensor_x),
                int(sensor_y),
                float(round(new_consumption, 2)),
                f"device:{associated_device}"
            )

            updated_smartmeters.add(sensor_name)

    # save updates
    if load_active:
        read_sensors = s_sensors
    else:
        sensors = s_sensors

    # update devices consumption
    update_devices_consumption(ui_canvas, d_devices, delta_seconds, timer_app_instance)

    # snapshot device->smartmeter 
    for device in d_devices:
        if isinstance(device, Device):
            dev_name, dev_type, state, current_cons = device.name, device.type, device.state, device.current_consumption
        else:
            dev_name, _, _, dev_type, _, state, _, _, current_cons, _ = device
        
        for sensor in s_sensors:
            sensor_type = sensor.type if isinstance(sensor, Sensor) else sensor[3]
            associated_dev = sensor.associated_device if isinstance(sensor, Sensor) else sensor[10]
            if sensor_type == "Smart Meter" and associated_dev == dev_name:
                sensor_name = sensor.name if isinstance(sensor, Sensor) else sensor[0]
                if sensor_name in updated_smartmeters:
                    continue

                if sensor_name not in sensor_states:
                    sensor_states[sensor_name] = {
                        'time': [],
                        'state': [],
                        'consumption': [],
                        'type': 'Smart Meter',
                        'associated_device': dev_name
                    }
                else:
                    sensor_states[sensor_name].setdefault('consumption', [])

                THRESHOLD_W = 1.0
                bin_state = 1 if (current_cons or 0.0) > THRESHOLD_W else 0

                sensor_x = sensor.x if isinstance(sensor, Sensor) else sensor[1]
                sensor_y = sensor.y if isinstance(sensor, Sensor) else sensor[2]

                _append_unique_sample(
                    sensor_states[sensor_name],
                    timestamp,
                    bin_state,
                    round(current_cons, 2)
                )

                log_sensor_event(
                    timestamp,
                    sensor_name,
                    "Smart Meter",
                    int(sensor_x),
                    int(sensor_y),
                    float(round(current_cons, 2)),
                    f"device:{dev_name}"
                )

    # per-second sampling (PIR/Switch/Weight)
    do_sample = PER_SECOND_SENSOR_SAMPLING
    types_to_sample = PER_SECOND_SENSOR_TYPES

    if do_sample and not fast:
        for sensor in s_sensors:
            sensor_type = sensor.type if isinstance(sensor, Sensor) else sensor[3]
            if sensor_type in types_to_sample:
                sensor_name = sensor.name if isinstance(sensor, Sensor) else sensor[0]
                sx = int(sensor.x) if isinstance(sensor, Sensor) else int(sensor[1])
                sy = int(sensor.y) if isinstance(sensor, Sensor) else int(sensor[2])
                try:
                    current_state = int(round(float(sensor.state if isinstance(sensor, Sensor) else sensor[7])))
                except Exception:
                    current_state = 0

                if sensor_name not in sensor_states:
                    sensor_states[sensor_name] = {'time': [], 'state': [], 'type': sensor_type}
                else:
                    sensor_states[sensor_name].setdefault('type', sensor_type)

                append_unique_binary(sensor_states[sensor_name], timestamp, current_state, sensor_type)
                log_sensor_event(timestamp, sensor_name, sensor_type, sx, sy, int(current_state), "per-second-sample")

    # normal scheduling
    if schedule_next and timer_app_instance.is_running and canvas is not None:
        canvas.after(1000, lambda: update_sensors(canvas, timer_app_instance, load_active, activity_label))

        