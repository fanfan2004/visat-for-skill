"""Adaptive Cruise Control (ACC) system.

Architecture
------------
The ACC is a two-loop (cascade) controller built on top of two PID
controllers:

* Speed loop  (pid_speed):    tracks the cruise set speed when no lead
                              vehicle is present, and tracks a desired
                              speed in follow mode.
* Distance loop (pid_distance): in follow mode, the distance error
                              (measured gap - desired gap) is converted
                              by the distance PID into a speed adjustment
                              applied on top of the lead vehicle's speed,
                              producing the desired speed for the inner
                              speed loop.

Modes
-----
* 'cruise'    : no lead vehicle detected (lead_speed is None) — regulate
                speed to the set speed.
* 'follow'    : lead vehicle present and time-to-collision (TTC) is safe
                — follow the lead while holding the desired gap.
* 'emergency' : lead present and TTC < emergency_ttc_threshold — apply
                maximum deceleration and reset the PID states.

Safety features
---------------
* Desired gap = min_distance + time_headway * ego_speed (>= min_distance).
* TTC-based emergency braking with the vehicle's maximum deceleration.
* Acceleration command clamped to [max_deceleration, max_acceleration].
* Anti-windup: integral increments that push a saturated command further
  into saturation are rolled back.
* PID states are reset on every mode transition to avoid stale windup.
"""

from pid_controller import PIDController


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


class AdaptiveCruiseControl:
    """ACC controller.

    Constructor: __init__(self, config) where config is the nested dict
    parsed from vehicle_params.yaml, e.g. config['acc_settings']['set_speed'].

    Method: compute(ego_speed, lead_speed, distance, dt)
        returns tuple (acceleration_cmd, mode, distance_error)
        - acceleration_cmd : float, m/s^2, clamped to vehicle limits
        - mode             : 'cruise' | 'follow' | 'emergency'
        - distance_error   : float (m) when a lead vehicle is present,
                             None in cruise mode
    """

    def __init__(self, config):
        vehicle = config.get('vehicle', {}) or {}
        acc = config.get('acc_settings', {}) or {}

        self.set_speed = float(acc.get('set_speed', 30.0))
        self.time_headway = float(acc.get('time_headway', 1.5))
        self.min_distance = float(acc.get('min_distance', 10.0))
        self.ttc_threshold = float(acc.get('emergency_ttc_threshold', 3.0))
        self.max_accel = float(vehicle.get('max_acceleration', 3.0))
        self.max_decel = float(vehicle.get('max_deceleration', -8.0))

        speed_cfg = config.get('pid_speed', {}) or {}
        dist_cfg = config.get('pid_distance', {}) or {}
        self.pid_speed = PIDController(
            speed_cfg.get('kp', 0.1), speed_cfg.get('ki', 0.01), speed_cfg.get('kd', 0.0))
        self.pid_distance = PIDController(
            dist_cfg.get('kp', 0.1), dist_cfg.get('ki', 0.01), dist_cfg.get('kd', 0.0))

        self.mode = 'cruise'
        self.last_ttc = None
        self.desired_distance = None

    def set_gains(self, speed_gains=None, distance_gains=None):
        """Override PID gains at runtime (used by the simulation to load the
        tuned gains from tuning_results.yaml).

        Each gains argument is a dict with optional keys kp, ki, kd.
        """
        if speed_gains:
            self.pid_speed.kp = float(speed_gains.get('kp', self.pid_speed.kp))
            self.pid_speed.ki = float(speed_gains.get('ki', self.pid_speed.ki))
            self.pid_speed.kd = float(speed_gains.get('kd', self.pid_speed.kd))
        if distance_gains:
            self.pid_distance.kp = float(distance_gains.get('kp', self.pid_distance.kp))
            self.pid_distance.ki = float(distance_gains.get('ki', self.pid_distance.ki))
            self.pid_distance.kd = float(distance_gains.get('kd', self.pid_distance.kd))

    def reset(self):
        """Reset both PID controllers and mode bookkeeping."""
        self.pid_speed.reset()
        self.pid_distance.reset()
        self.mode = 'cruise'
        self.last_ttc = None
        self.desired_distance = None

    def _reset_pids(self):
        self.pid_speed.reset()
        self.pid_distance.reset()

    def _speed_pid_cmd(self, error, dt):
        """Run the speed PID, clamp the command to vehicle limits and roll
        back integral action that pushed the command into saturation."""
        raw = self.pid_speed.compute(error, dt)
        cmd = _clamp(raw, self.max_decel, self.max_accel)
        if cmd != raw:
            saturated_high = raw > self.max_accel and error > 0
            saturated_low = raw < self.max_decel and error < 0
            if saturated_high or saturated_low:
                self.pid_speed.integral -= error * dt
        return cmd

    def compute(self, ego_speed, lead_speed, distance, dt):
        """Compute one ACC control step.

        ego_speed  : float — current ego speed (m/s)
        lead_speed : float or None — lead vehicle speed (m/s); None if no
                     vehicle is detected ahead
        distance   : float — measured gap to the lead vehicle (m); ignored
                     (may be None) when lead_speed is None
        dt         : float — timestep (s)

        Returns (acceleration_cmd, mode, distance_error).
        """
        ego_speed = float(ego_speed)

        # ---- cruise mode: no lead vehicle detected ----
        if lead_speed is None:
            if self.mode != 'cruise':
                self._reset_pids()
            self.mode = 'cruise'
            self.desired_distance = None
            error = self.set_speed - ego_speed
            cmd = self._speed_pid_cmd(error, dt)
            return cmd, 'cruise', None

        # ---- lead vehicle present ----
        desired = max(self.min_distance,
                      self.min_distance + self.time_headway * ego_speed)
        self.desired_distance = desired
        distance_error = float(distance) - desired

        closing = ego_speed - float(lead_speed)
        if closing > 1e-9 and distance > 0:
            ttc = distance / closing
        else:
            ttc = float('inf')
        self.last_ttc = ttc

        # ---- emergency mode: TTC below the safety threshold ----
        if ttc < self.ttc_threshold:
            if self.mode != 'emergency':
                self._reset_pids()
            self.mode = 'emergency'
            # Full braking with the vehicle's maximum deceleration.
            return self.max_decel, 'emergency', distance_error

        # ---- follow mode: cascade distance -> speed control ----
        if self.mode != 'follow':
            self._reset_pids()
        self.mode = 'follow'

        # Outer loop: distance PID output is a speed adjustment on top of
        # the lead vehicle's speed. The integral term drives the gap error
        # to zero in steady state.
        v_adjust = self.pid_distance.compute(distance_error, dt)
        v_desired = _clamp(float(lead_speed) + v_adjust, 0.0, self.set_speed)

        # Inner loop: speed PID tracks the desired speed.
        cmd = self._speed_pid_cmd(v_desired - ego_speed, dt)
        return cmd, 'follow', distance_error
