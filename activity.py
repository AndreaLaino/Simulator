import re

from common import sensor_states
from device import devices
from door import doors
from log import log_activity_start, log_activity_end, log_end_of_simulation
from point import points
from read import coordinates, read_devices, read_sensors, read_walls_coordinates, read_doors
from sensor import sensors
from utils import find_closest_sensor_within_fov
from wall import walls_coordinates

FOV_ANGLE = 60  # degrees for PIR field-of-view checks
RADIUS_STANDARD = 150   # px distance for "closest sensor within FOV"

activity_sessions = {}
current_activities = {}

exit_triggered = False
exit_time = 0
prev_entry_state = None # previous state of the input switch to detect fronts


meal_detection_start = {
    "breakfast": None,
    "lunch": None,
    "dinner": None
}
meal_active = None
MEAL_MIN_DURATION = 10  # simulated seconds to confirm meal
SLEEP_MIN_DURATION = 10  # simulated seconds with Weight=1 near bed
sleep_weight_start = {}  # sleep_weight_start: timers per Weight sensor near bed
# index of the last edge 1->0 already managed for the "entrance" switch
exit_last_edge_idx = -1


def monitor_activities(canvas, load_active, activity_label, timer_app_instance):
    if load_active:
        p_points = coordinates
        d_devices = read_devices
        s_sensors = read_sensors
        walls = read_walls_coordinates
        d_doors = read_doors
    else:
        p_points = points
        d_devices = devices
        s_sensors = sensors
        walls = walls_coordinates
        d_doors = doors

    if timer_app_instance.is_running:
        now = timer_app_instance.get_simulated_time()
        detected = set()

        detectors = [
            lambda: detect_exiting_home(sensor_states, s_sensors, timer_app_instance),
            lambda: detect_entering_home(sensor_states, s_sensors, timer_app_instance, activity_label),
            lambda: detect_sleeping(sensor_states, s_sensors, p_points, timer_app_instance),
            lambda: detect_cooking(sensor_states, d_devices, s_sensors, walls, d_doors),
            lambda: detect_meal(sensor_states, s_sensors, d_devices, timer_app_instance),
            lambda: detect_laundry(sensor_states, d_devices),
            lambda: detect_dishwasher(sensor_states, d_devices),
            lambda: detect_office(sensor_states, d_devices),
        ]

        for detect in detectors:
            act = detect()
            if act:
                detected.add(act)

        update_activity_state(now, detected, activity_label)

        canvas.after(1000, monitor_activities, canvas, load_active, activity_label, timer_app_instance)

def update_activity_state(current_time, detected_activities, activity_label):
    global current_activities, activity_sessions

    for act in detected_activities:
        if act not in current_activities:
            current_activities[act] = current_time
            log_activity_start(act, current_time)

    ended = [act for act in current_activities if act not in detected_activities]
    for act in ended:
        start = current_activities.pop(act)
        activity_sessions.setdefault(act, []).append({"start": start, "end": current_time})
        log_activity_end(act, current_time)

    # update activity label
    active = list(current_activities.keys())
    if activity_label:
        if active:
            activity_label.config(text="Activity: " + ", ".join(sorted(active)))
        else:
            activity_label.config(text="Activity: None")

def close_current_activity(timer_app_instance, activity_label=None):
    global current_activities, activity_sessions

    now = timer_app_instance.get_simulated_time()

    for act, start in list(current_activities.items()):
        activity_sessions.setdefault(act, []).append({"start": start, "end": now})
        log_activity_end(act, now)
    current_activities.clear()

    if activity_label:
        activity_label.config(text="Activity: None")

    log_end_of_simulation(now)


def detect_cooking(sensor_states, devices, sensors, walls, doors):
    for device in devices:
        name, x, y, type, _, state, *_ = device
        if re.match(r'^oven\d*$', type, re.IGNORECASE) and state == 1:
            pir = find_closest_sensor_within_fov((x, y), sensors, walls, doors, RADIUS_STANDARD, FOV_ANGLE)
            if pir:
                pir_state = sensor_states.get(pir[0], {}).get('state', [])
                if pir_state and pir_state[-1] == 1:
                    return "cooking"
    return None

def detect_laundry(sensor_states, devices):
    for name, data in sensor_states.items():
        if data.get('type') == "Smart Meter":
            assoc = data.get('associated_device')
            state = data['state'][-1] if data['state'] else 0
            if assoc:
                for d in devices:
                    if d[0] == assoc and d[3].lower() == "washing_machine" and state > 0:
                        return "laundry"
    return None

def detect_dishwasher(sensor_states, devices):
    for name, data in sensor_states.items():
        if data.get('type') == "Smart Meter":
            assoc = data.get('associated_device')
            state = data['state'][-1] if data['state'] else 0
            if assoc:
                for d in devices:
                    if d[0] == assoc and d[3].lower() == "dishwasher" and state > 0:
                        return "dishwasher"
    return None

def detect_office(sensor_states, devices):
    for name, data in sensor_states.items():
        if data.get('type') == "Smart Meter":
            assoc = data.get('associated_device')
            state = data['state'][-1] if data['state'] else 0
            if assoc:
                for d in devices:
                    if d[0] == assoc and d[3].lower() == "computer" and state > 0:
                        return "office"
    return None

def detect_exiting_home(sensor_states, sensors, timer_app_instance):
    global exit_triggered, exit_time, exit_activated, exit_last_edge_idx

    # intialization
    if 'exit_activated' not in globals():
        exit_activated = False
    if 'exit_triggered' not in globals():
        exit_triggered = False
    if 'exit_time' not in globals():
        exit_time = None
    if 'exit_last_edge_idx' not in globals():
        exit_last_edge_idx = -1

    now = timer_app_instance.get_simulated_time()


    entrance_state = None
    entrance_name = None
    for s in sensors:
        if s[0].lower() == "entrance" and s[3].lower() == "switch":
            entrance_name = s[0]
            entrance_state = sensor_states.get(entrance_name, {}).get("state", [])
            break

    # Detect last edge 1->0 (if it exists)
    if entrance_state and len(entrance_state) >= 2 and not exit_activated:
        last_edge_idx = None
        # I scroll through the entire sequence: it handles cases [1,0,1,0] well in the same second
        for i in range(1, len(entrance_state)):
            if entrance_state[i-1] == 1 and entrance_state[i] == 0:
                last_edge_idx = i

        if last_edge_idx is not None and last_edge_idx > exit_last_edge_idx:
            exit_last_edge_idx = last_edge_idx
            exit_triggered = True
            exit_time = timer_app_instance.elapsed_time

    #  After the trigger: wait 5s and check PIR all at 0
    if exit_triggered and not exit_activated:
        delta = (timer_app_instance.elapsed_time - exit_time).total_seconds()
        if delta >= 5:
            exit_triggered = False
            # checks that all PIRs are at 0 (last seen state)
            all_zero = True
            for s in sensors:
                if s[3] == "PIR":
                    seq = sensor_states.get(s[0], {}).get("state", [])
                    if seq and seq[-1] == 1:
                        all_zero = False
                        break
            if all_zero:
                exit_activated = True
                return "Leaving home"

    # If already out, keep reporting activity
    if exit_activated:
        return "Leaving home"

    return None

def detect_entering_home(sensor_states, sensors, timer_app_instance, activity_label=None):
    global returning_triggered, returning_time, exit_activated, prev_entry_state
    global exit_triggered, exit_time, exit_last_edge_idx

    if 'exit_activated' not in globals():
        exit_activated = False
    if 'returning_triggered' not in globals():
        returning_triggered = False
    if 'returning_time' not in globals():
        returning_time = None
    if 'prev_entry_state' not in globals():
        prev_entry_state = None
    if 'exit_triggered' not in globals():
        exit_triggered = False
    if 'exit_time' not in globals():
        exit_time = None
    if 'exit_last_edge_idx' not in globals():
        exit_last_edge_idx = -1

    # If I wasn't away from home, I can't return home
    if not exit_activated:
        # prev_entry_state so as not to lose the first useful front
        for s in sensors:
            if s[0].lower() == "entrance" and s[3].lower() == "switch":
                curr = sensor_states.get(s[0], {}).get("state", [])
                prev_entry_state = (curr[-1] if curr else 0)
                break
        return None

    # Current entrance switch state
    entrance_state = None
    for s in sensors:
        if s[0].lower() == "entrance" and s[3].lower() == "switch":
            entrance_state = sensor_states.get(s[0], {}).get("state", [])
            break

    curr_entrance = entrance_state[-1] if entrance_state else 0
    if prev_entry_state is None:
        prev_entry_state = curr_entrance

    # edge 1->0 to start the indent window
    if prev_entry_state == 1 and curr_entrance == 0 and not returning_triggered:
        returning_triggered = True
        returning_time = timer_app_instance.elapsed_time

    # update for next interaction
    prev_entry_state = curr_entrance

    # At least one PIR must be activated within 5 simulated seconds
    if returning_triggered:
        delta = (timer_app_instance.elapsed_time - returning_time).total_seconds()
        if delta > 5:
            # timeout expired
            returning_triggered = False
        else:
            active_pir = any(
                (sensor_states.get(s[0], {}).get("state", []) and
                 sensor_states.get(s[0], {}).get("state")[-1] == 1)
                for s in sensors if s[3] == "PIR"
            )
            if active_pir:
                returning_triggered = False
                exit_activated = False

                # Reset
                exit_triggered = False
                exit_time = None


                try:
                    last_edge_idx = None
                    if entrance_state and len(entrance_state) >= 2:
                        for i in range(1, len(entrance_state)):
                            if entrance_state[i-1] == 1 and entrance_state[i] == 0:
                                last_edge_idx = i
                    if last_edge_idx is not None:
                        exit_last_edge_idx = max(exit_last_edge_idx, last_edge_idx)
                except Exception:
                    pass

                if activity_label:
                    activity_label.config(text="Activity: returning home")
                    def reset_label():
                        if current_activities:
                            activity_label.config(
                                text="Activity: " + ", ".join(sorted(current_activities.keys()))
                            )
                        else:
                            activity_label.config(text="Activity: None")
                    activity_label.after(2000, reset_label)

                return "returning home"

    return None


def detect_sleeping(sensor_states, sensors, points, timer_app_instance):

    global sleep_weight_start

    bed_pattern = re.compile(r'^bed\d*$', re.IGNORECASE)

    # search all 'bed*' points
    beds = [(name, x, y) for (name, x, y) in points if bed_pattern.match(name)]

    def dist(ax, ay, bx, by):
        return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5

    any_near_bed_active = False

    for _, lx, ly in beds:
        # search weight sensor near bed
        for s in sensors:
            name, sx, sy, type, *_ = s
            if type == "Weight" and dist(lx, ly, sx, sy) < 30:
                state_seq = sensor_states.get(name, {}).get('state', [])
                active = bool(state_seq and state_seq[-1] == 1)

                if active:
                    any_near_bed_active = True
                    # start timer for this sensor
                    if name not in sleep_weight_start:
                        sleep_weight_start[name] = timer_app_instance.elapsed_time
                    else:
                        delta = (timer_app_instance.elapsed_time - sleep_weight_start[name]).total_seconds()
                        if delta >= SLEEP_MIN_DURATION:
                            return "sleeping"
                else:
                    # reset timer
                    if name in sleep_weight_start:
                        del sleep_weight_start[name]




def detect_meal(sensor_states, sensors, devices, timer_app_instance):
    global meal_detection_start, meal_active
    TABLE_RADIUS = 40  # max distance weight - table

    # find table coordinates
    table_coords = None
    table_pattern = re.compile(r'^table\d*$', re.IGNORECASE)
    for name, x, y in (points + coordinates):
        if table_pattern.match(name):
            table_coords = (x, y)
            break

    # search for Active Weight sensor near the table
    def weight_active_near_table():
        if not table_coords:
            return False
        tx, ty = table_coords
        for s in sensors:
            name, sx, sy, type, *_ = s
            if type == "Weight":
                dist = ((tx - sx)**2 + (ty - sy)**2) ** 0.5
                if dist <= TABLE_RADIUS:
                    state = sensor_states.get(name, {}).get("state", [])
                    if state and state[-1] == 1:
                        return True
        return False


    time_str = timer_app_instance.get_simulated_time()
    try:
        hour, _ = map(int, time_str.split(":"))
    except:
        hour = 0

    slot = None
    if 7 <= hour < 9:
        slot = "breakfast"
    elif 12 <= hour < 14:
        slot = "lunch"
    elif 20 <= hour < 22:
        slot = "dinner"

    if slot:
        for d in devices:
            name, x, y, type, _, state, *_ = d
            # oven off
            if type.lower() == "oven" and state == 0:
                pir = find_closest_sensor_within_fov((x, y), sensors, walls_coordinates, doors, RADIUS_STANDARD, FOV_ANGLE)
                if pir:
                    state = sensor_states.get(pir[0], {}).get("state", [])
                    if state and state[-1] == 1:
                        if not weight_active_near_table():
                            return None
                        if meal_active == slot:
                            return slot
                        if meal_detection_start[slot] is None:
                            meal_detection_start[slot] = timer_app_instance.elapsed_time
                        else:
                            delta = (timer_app_instance.elapsed_time - meal_detection_start[slot]).total_seconds()
                            if delta >= MEAL_MIN_DURATION:
                                meal_detection_start[slot] = None
                                meal_active = slot
                                return slot
                        return None

        # reset
        meal_detection_start[slot] = None
        if meal_active == slot:
            meal_active = None
    else:
        for key in meal_detection_start:
            meal_detection_start[key] = None
        meal_active = None

    return None
