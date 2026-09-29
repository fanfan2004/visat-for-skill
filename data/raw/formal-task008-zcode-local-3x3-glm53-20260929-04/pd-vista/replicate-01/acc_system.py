"""Adaptive Cruise Control (ACC) system and longitudinal vehicle simulation.

Architecture
------------
``AdaptiveCruiseControl`` is the supervisory controller.  It selects one of
three modes at every timestep and routes the appropriate PID loop:

* ``cruise``    - no lead vehicle detected (``lead_speed is None``): the speed
  PID regulates ego speed to ``acc_settings['set_speed']``.
* ``follow``    - a lead vehicle is present: the distance PID regulates the
  gap to the spacing policy ``time_headway * ego_speed + min_distance``.
* ``emergency`` - time-to-collision TTC = distance / closing_speed falls below
  ``emergency_ttc_threshold``: full braking at ``max_deceleration``.

Safety features
---------------
* acceleration commands are clamped to ``[max_deceleration, max_acceleration]``,
* PID output limits + conditional-integration anti-windup (see pid_controller),
* in follow mode the ACC never commands speed above the driver-set speed,
* the spacing policy never requests a gap below ``min_distance``,
* ego speed is floored at 0 (no reversing).

Plant
-----
``EgoVehicle`` is a simple longitudinal model: acceleration integrates to
speed, speed integrates to position.  The lead vehicle trajectory is taken
from ``sensor_data.csv`` (``lead_speed``); the lead position starts at the
first detection using the measured ``distance`` and is then integrated with
the lead speed, so the closed loop reacts to ego braking/acceleration.
"""

import csv

from pid_controller import PIDController


class AdaptiveCruiseControl:
    """Supervisory ACC controller with cruise / follow / emergency modes."""

    MODE_CRUISE = "cruise"
    MODE_FOLLOW = "follow"
    MODE_EMERGENCY = "emergency"

    def __init__(self, config):
        vehicle = config["vehicle"]
        acc = config["acc_settings"]
        self.mass = float(vehicle["mass"])
        self.max_acceleration = float(vehicle["max_acceleration"])
        self.max_deceleration = float(vehicle["max_deceleration"])
        self.set_speed = float(acc["set_speed"])
        self.time_headway = float(acc["time_headway"])
        self.min_distance = float(acc["min_distance"])
        self.emergency_ttc_threshold = float(acc["emergency_ttc_threshold"])

        limits = (self.max_deceleration, self.max_acceleration)
        sp = config["pid_speed"]
        dp = config["pid_distance"]
        self.speed_pid = PIDController(sp["kp"], sp["ki"], sp["kd"], out_limits=limits)
        self.distance_pid = PIDController(dp["kp"], dp["ki"], dp["kd"], out_limits=limits)
        self.mode = self.MODE_CRUISE

    def reset(self):
        """Reset both PID loops and return to cruise mode."""
        self.speed_pid.reset()
        self.distance_pid.reset()
        self.mode = self.MODE_CRUISE

    def desired_distance(self, ego_speed):
        """Time-headway model: standstill gap plus a speed-proportional gap."""
        return ego_speed * self.time_headway + self.min_distance

    def time_to_collision(self, ego_speed, lead_speed, distance):
        closing = ego_speed - lead_speed
        if closing > 1e-9:
            return distance / closing
        return None  # not approaching

    def compute(self, ego_speed, lead_speed, distance, dt):
        """One control step.

        ego_speed  : current ego speed [m/s]
        lead_speed : lead vehicle speed [m/s] or None when no lead detected
        distance   : current gap to the lead vehicle [m] (unused in cruise)
        dt         : timestep [s]
        returns (acceleration_cmd [m/s^2], mode, distance_error [m] or None)

        distance_error = desired_distance - actual_distance, i.e. positive
        when the ego is too close and must slow down.
        """
        if lead_speed is None:
            mode = self.MODE_CRUISE
            distance_error = None
            if self.mode != mode:
                self.reset()
            error = self.set_speed - ego_speed
            accel = self.speed_pid.compute(error, dt)
        else:
            desired = self.desired_distance(ego_speed)
            distance_error = desired - distance
            ttc = self.time_to_collision(ego_speed, lead_speed, distance)
            if ttc is not None and ttc < self.emergency_ttc_threshold:
                mode = self.MODE_EMERGENCY
                accel = self.max_deceleration
                # keep the distance PID state fresh (anti-windup keeps its
                # integrator bounded) so re-entry into follow is smooth
                self.distance_pid.compute(distance_error, dt)
            else:
                mode = self.MODE_FOLLOW
                # positive distance_error (too close) must brake -> negative gain
                accel = -self.distance_pid.compute(distance_error, dt)
                # the ACC never drives faster than the driver-set speed
                if accel > 0 and ego_speed + accel * dt > self.set_speed:
                    accel = (self.set_speed - ego_speed) / dt
                    if accel < 0.0:
                        accel = 0.0

        accel = max(self.max_deceleration, min(self.max_acceleration, accel))
        self.mode = mode
        return accel, mode, distance_error


class EgoVehicle:
    """Simple longitudinal ego-vehicle model (acceleration -> speed -> position)."""

    def __init__(self, config, initial_speed=0.0, initial_position=0.0):
        vehicle = config["vehicle"]
        self.max_acceleration = float(vehicle["max_acceleration"])
        self.max_deceleration = float(vehicle["max_deceleration"])
        self.speed = float(initial_speed)
        self.position = float(initial_position)

    def step(self, acceleration_cmd, dt):
        accel = max(self.max_deceleration, min(self.max_acceleration, acceleration_cmd))
        self.speed += accel * dt
        if self.speed < 0.0:
            self.speed = 0.0  # cannot drive backwards
        self.position += self.speed * dt
        return self.speed


def load_sensor_data(path):
    """Read sensor_data.csv -> list of dicts with float time, lead_speed, distance (None if empty)."""
    samples = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            lead = row.get("lead_speed")
            dist = row.get("distance")
            samples.append(
                {
                    "time": float(row["time"]),
                    "lead_speed": float(lead) if lead not in (None, "") else None,
                    "distance": float(dist) if dist not in (None, "") else None,
                }
            )
    return samples


def run_simulation(config, sensor_data):
    """Run the full closed-loop ACC simulation.

    sensor_data : list of dicts as returned by load_sensor_data (one sample
    per timestep, containing the lead-vehicle scenario).  Returns a list of
    result dicts with keys time, ego_speed, acceleration_cmd, mode,
    distance_error, distance, ttc.
    """
    dt = float(config["simulation"]["dt"])
    controller = AdaptiveCruiseControl(config)
    ego = EgoVehicle(config)
    lead_position = None
    results = []

    for sample in sensor_data:
        t = sample["time"]
        lead_speed = sample["lead_speed"]
        measured_distance = sample["distance"]

        if lead_speed is not None:
            if lead_position is None:
                # first detection: anchor the lead at the measured gap
                lead_position = ego.position + measured_distance
            distance = lead_position - ego.position
        else:
            distance = None
            lead_position = None

        accel, mode, distance_error = controller.compute(ego.speed, lead_speed, distance, dt)

        ttc = ""
        if lead_speed is not None:
            closing = ego.speed - lead_speed
            ttc = distance / closing if closing > 1e-9 else ""
            ttc = round(ttc, 3) if ttc != "" else ""

        results.append(
            {
                "time": round(t, 1),
                "ego_speed": round(ego.speed, 3),
                "acceleration_cmd": round(accel, 3),
                "mode": mode,
                "distance_error": "" if distance_error is None else round(distance_error, 3),
                "distance": "" if distance is None else round(distance, 3),
                "ttc": ttc,
            }
        )

        # integrate the plant and the lead trajectory to the next step
        ego.step(accel, dt)
        if lead_speed is not None:
            lead_position += lead_speed * dt

    return results


def _fmt(value, nd=3):
    """Round for output and normalize negative zero."""
    v = round(float(value), nd)
    return 0.0 if v == 0 else v


def write_results_csv(results, path):
    fields = ["time", "ego_speed", "acceleration_cmd", "mode", "distance_error", "distance", "ttc"]
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(fields)
        for r in results:
            row = []
            for k in fields:
                v = r[k]
                if v == "" or v is None:
                    row.append("")
                elif k == "mode":
                    row.append(v)
                elif k == "time":
                    row.append(_fmt(v, 1))
                else:
                    row.append(_fmt(v))
            writer.writerow(row)
