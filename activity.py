import re

from door import doors
from log import log_activity_start, log_activity_end, log_end_of_simulation
from house_state import HouseState
from utils import find_closest_sensor_within_fov
from wall import walls_coordinates

FOV_ANGLE = 60  # degrees for PIR field-of-view checks
RADIUS_STANDARD = 150   # px distance for "closest sensor within FOV"
MEAL_MIN_DURATION = 10  # simulated seconds to confirm meal
SLEEP_MIN_DURATION = 10  # simulated seconds with Weight=1 near bed
EXIT_MOTION_WINDOW = 10  # simulated seconds of recent PIR motion required before leaving home


def _activity_state(house_state: HouseState) -> dict:
    state = house_state.activity_state()
    state.setdefault("activity_sessions", {})
    state.setdefault("current_activities", {})
    state.setdefault("exit_triggered", False)
    state.setdefault("exit_time", None)
    state.setdefault("exit_activated", False)
    state.setdefault("prev_entry_state", None)
    state.setdefault(
        "meal_detection_start",
        {
            "breakfast": None,
            "lunch": None,
            "dinner": None,
        },
    )
    state.setdefault("meal_active", None)
    state.setdefault("sleep_weight_start", {})
    state.setdefault("exit_last_edge_idx", -1)
    state.setdefault("last_pir_motion_time", None)
    return state


def _activity_log_state(house_state: HouseState) -> dict:
    state = house_state.activity_log_state()
    state.setdefault("activity_log", [])
    state.setdefault("active_activities", {})
    return state


def monitor_activities(canvas, activity_label, timer_app_instance, sensor_states_store, house_state: HouseState, runtime_sources: dict):
    state = _activity_state(house_state)
    log_state = _activity_log_state(house_state)

    p_points = runtime_sources["points"]
    d_devices = runtime_sources["devices"]
    s_sensors = runtime_sources["sensors"]
    walls = runtime_sources["walls"]
    d_doors = runtime_sources["doors"]

    if timer_app_instance.is_running:
        now = timer_app_instance.get_simulated_time()
        detected = set()

        detectors = [
            lambda: detect_exiting_home(sensor_states_store, s_sensors, timer_app_instance, state),
            lambda: detect_entering_home(sensor_states_store, s_sensors, timer_app_instance, activity_label, state),
            lambda: detect_sleeping(sensor_states_store, s_sensors, p_points, timer_app_instance, state),
            lambda: detect_cooking(sensor_states_store, d_devices, s_sensors, walls, d_doors),
            lambda: detect_meal(sensor_states_store, s_sensors, d_devices, p_points, timer_app_instance, state),
            lambda: detect_laundry(sensor_states_store, d_devices),
            lambda: detect_dishwasher(sensor_states_store, d_devices),
            lambda: detect_office(sensor_states_store, d_devices),
        ]

        for detect in detectors:
            act = detect()
            if act:
                detected.add(act)

        update_activity_state(now, detected, activity_label, state, log_state)

        canvas.after(1000, monitor_activities, canvas, activity_label, timer_app_instance, sensor_states_store, house_state, runtime_sources)

def update_activity_state(current_time, detected_activities, activity_label, state: dict, log_state: dict):
    current_activities = state["current_activities"]
    activity_sessions = state["activity_sessions"]

    for act in detected_activities:
        if act not in current_activities:
            current_activities[act] = current_time
            log_activity_start(act, current_time, log_state)

    ended = [act for act in current_activities if act not in detected_activities]
    for act in ended:
        start = current_activities.pop(act)
        activity_sessions.setdefault(act, []).append({"start": start, "end": current_time})
        log_activity_end(act, current_time, log_state)

    # update activity label
    active = list(current_activities.keys())
    if activity_label:
        if active:
            activity_label.config(text="Activity: " + ", ".join(sorted(active)))
        else:
            activity_label.config(text="Activity: None")

def close_current_activity(timer_app_instance, activity_label=None, house_state: HouseState | None = None):
    state = _activity_state(house_state) if house_state is not None else {
        "current_activities": {},
        "activity_sessions": {},
    }
    log_state = _activity_log_state(house_state) if house_state is not None else {
        "activity_log": [],
        "active_activities": {},
    }
    current_activities = state["current_activities"]
    activity_sessions = state["activity_sessions"]

    now = timer_app_instance.get_simulated_time()

    for act, start in list(current_activities.items()):
        activity_sessions.setdefault(act, []).append({"start": start, "end": now})
        log_activity_end(act, now, log_state)
    current_activities.clear()

    if activity_label:
        activity_label.config(text="Activity: None")

    log_end_of_simulation(now, log_state)


def detect_cooking(sensor_states, devices, sensors, walls, doors):
    for device in devices:
        name, x, y, type, state = device.name, device.x, device.y, device.type, device.state
        if re.match(r'^oven\d*$', type, re.IGNORECASE) and state == 1:
            pir = find_closest_sensor_within_fov((x, y), sensors, walls, doors, RADIUS_STANDARD, FOV_ANGLE)
            if pir:
                pir_state = sensor_states.get(pir.name, {}).get('state', [])
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
                    if d.name == assoc and d.type.lower() == "washing_machine" and state > 0:
                        return "laundry"
    return None

def detect_dishwasher(sensor_states, devices):
    for name, data in sensor_states.items():
        if data.get('type') == "Smart Meter":
            assoc = data.get('associated_device')
            state = data['state'][-1] if data['state'] else 0
            if assoc:
                for d in devices:
                    if d.name == assoc and d.type.lower() == "dishwasher" and state > 0:
                        return "dishwasher"
    return None

def detect_office(sensor_states, devices):
    for name, data in sensor_states.items():
        if data.get('type') == "Smart Meter":
            assoc = data.get('associated_device')
            state = data['state'][-1] if data['state'] else 0
            if assoc:
                for d in devices:
                    if d.name == assoc and d.type.lower() == "computer" and state > 0:
                        return "office"
    return None

def detect_exiting_home(sensor_states, sensors, timer_app_instance, state: dict):
    exit_triggered = bool(state.get("exit_triggered", False))
    exit_time = state.get("exit_time")
    exit_activated = bool(state.get("exit_activated", False))
    exit_last_edge_idx = int(state.get("exit_last_edge_idx", -1))
    last_pir_motion_time = state.get("last_pir_motion_time")

    now = timer_app_instance.get_simulated_time()
    now_elapsed = timer_app_instance.elapsed_time

    any_pir_active = False
    for s in sensors:
        if s.type == "PIR":
            seq = sensor_states.get(s.name, {}).get("state", [])
            if seq and seq[-1] == 1:
                any_pir_active = True
                break
    if any_pir_active:
        last_pir_motion_time = now_elapsed


    entrance_state = None
    entrance_name = None
    for s in sensors:
        if s.name.lower() == "entrance" and s.type.lower() == "switch":
            entrance_name = s.name
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
            recent_motion = (
                last_pir_motion_time is not None and
                (now_elapsed - last_pir_motion_time).total_seconds() <= EXIT_MOTION_WINDOW
            )
            if recent_motion:
                exit_triggered = True
                exit_time = now_elapsed

    #  After the trigger: wait 5s and check PIR all at 0
    if exit_triggered and not exit_activated:
        delta = (now_elapsed - exit_time).total_seconds()
        if delta >= 5:
            exit_triggered = False
            # checks that all PIRs are at 0 (last seen state)
            all_zero = True
            for s in sensors:
                if s.type == "PIR":
                    seq = sensor_states.get(s.name, {}).get("state", [])
                    if seq and seq[-1] == 1:
                        all_zero = False
                        break
            if all_zero:
                exit_activated = True
                state["exit_triggered"] = exit_triggered
                state["exit_time"] = exit_time
                state["exit_activated"] = exit_activated
                state["exit_last_edge_idx"] = exit_last_edge_idx
                state["last_pir_motion_time"] = last_pir_motion_time
                return "Leaving home"

    # If already out, keep reporting activity
    if exit_activated:
        state["exit_triggered"] = exit_triggered
        state["exit_time"] = exit_time
        state["exit_activated"] = exit_activated
        state["exit_last_edge_idx"] = exit_last_edge_idx
        state["last_pir_motion_time"] = last_pir_motion_time
        return "Leaving home"

    state["exit_triggered"] = exit_triggered
    state["exit_time"] = exit_time
    state["exit_activated"] = exit_activated
    state["exit_last_edge_idx"] = exit_last_edge_idx
    state["last_pir_motion_time"] = last_pir_motion_time
    return None

def detect_entering_home(sensor_states, sensors, timer_app_instance, activity_label=None, state: dict | None = None):
    if state is None:
        state = {}

    returning_triggered = bool(state.get("returning_triggered", False))
    returning_time = state.get("returning_time")
    exit_activated = bool(state.get("exit_activated", False))
    prev_entry_state = state.get("prev_entry_state")
    exit_triggered = bool(state.get("exit_triggered", False))
    exit_time = state.get("exit_time")
    exit_last_edge_idx = int(state.get("exit_last_edge_idx", -1))

    # If I wasn't away from home, I can't return home
    if not exit_activated:
        # prev_entry_state so as not to lose the first useful front
        for s in sensors:
            if s.name.lower() == "entrance" and s.type.lower() == "switch":
                curr = sensor_states.get(s.name, {}).get("state", [])
                prev_entry_state = (curr[-1] if curr else 0)
                state["prev_entry_state"] = prev_entry_state
                break
        return None

    # Current entrance switch state
    entrance_state = None
    for s in sensors:
        if s.name.lower() == "entrance" and s.type.lower() == "switch":
            entrance_state = sensor_states.get(s.name, {}).get("state", [])
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
    state["prev_entry_state"] = prev_entry_state

    # At least one PIR must be activated within 5 simulated seconds
    if returning_triggered:
        delta = (timer_app_instance.elapsed_time - returning_time).total_seconds()
        if delta > 5:
            # timeout expired
            returning_triggered = False
        else:
            active_pir = any(
                (sensor_states.get(s.name, {}).get("state", []) and
                 sensor_states.get(s.name, {}).get("state")[-1] == 1)
                for s in sensors if s.type == "PIR"
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
                        current_activities = state.get("current_activities", {})
                        if current_activities:
                            activity_label.config(
                                text="Activity: " + ", ".join(sorted(current_activities.keys()))
                            )
                        else:
                            activity_label.config(text="Activity: None")
                    activity_label.after(2000, reset_label)

                state["returning_triggered"] = returning_triggered
                state["returning_time"] = returning_time
                state["exit_activated"] = exit_activated
                state["exit_triggered"] = exit_triggered
                state["exit_time"] = exit_time
                state["exit_last_edge_idx"] = exit_last_edge_idx

                return "returning home"

    state["returning_triggered"] = returning_triggered
    state["returning_time"] = returning_time
    state["exit_activated"] = exit_activated
    state["exit_triggered"] = exit_triggered
    state["exit_time"] = exit_time
    state["exit_last_edge_idx"] = exit_last_edge_idx
    return None


def detect_sleeping(sensor_states, sensors, points, timer_app_instance, state: dict):
    sleep_weight_start = state.setdefault("sleep_weight_start", {})

    bed_pattern = re.compile(r'^bed\d*$', re.IGNORECASE)

    # search all 'bed*' points
    beds = [(point.name, point.x, point.y) for point in points if bed_pattern.match(point.name)]

    def dist(ax, ay, bx, by):
        return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5

    any_near_bed_active = False

    for _, lx, ly in beds:
        # search weight sensor near bed
        for s in sensors:
            name, sx, sy, type = s.name, s.x, s.y, s.type
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




def detect_meal(sensor_states, sensors, devices, points_source, timer_app_instance, state: dict):
    meal_detection_start = state.setdefault(
        "meal_detection_start",
        {"breakfast": None, "lunch": None, "dinner": None},
    )
    meal_active = state.get("meal_active")
    TABLE_RADIUS = 40  # max distance weight - table

    # find table coordinates
    table_coords = None
    table_pattern = re.compile(r'^table\d*$', re.IGNORECASE)
    for point in points_source:
        if table_pattern.match(point.name):
            table_coords = (point.x, point.y)
            break

    # search for Active Weight sensor near the table
    def weight_active_near_table():
        if not table_coords:
            return False
        tx, ty = table_coords
        for s in sensors:
            name, sx, sy, type = s.name, s.x, s.y, s.type
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
            name, x, y, type, state = d.name, d.x, d.y, d.type, d.state
            # oven off
            if type.lower() == "oven" and state == 0:
                pir = find_closest_sensor_within_fov((x, y), sensors, walls_coordinates, doors, RADIUS_STANDARD, FOV_ANGLE)
                if pir:
                    pir_state = sensor_states.get(pir.name, {}).get("state", [])
                    if pir_state and pir_state[-1] == 1:
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
                                state["meal_active"] = meal_active
                                return slot
                        return None

        # reset
        meal_detection_start[slot] = None
        if meal_active == slot:
            meal_active = None
            state["meal_active"] = meal_active
    else:
        for key in meal_detection_start:
            meal_detection_start[key] = None
        meal_active = None
        state["meal_active"] = meal_active

    return None
