"""Adaptive Cruise Control (ACC) system.

Implements the ACC state machine and speed/distance control layers:

- 'cruise': no lead vehicle detected; regulate ego speed to the set speed
  with the speed PID.
- 'follow': lead vehicle present and time-to-collision (TTC) is safe;
  target the lead vehicle's speed plus a gap-correction term produced by
  the distance PID. Gap closing is speed-limited so the closing TTC never
  approaches the emergency threshold, and the target never exceeds the set
  speed.
- 'emergency': TTC below the configured threshold; command a controlled
  constant-deceleration brake profile sized to bring the ego to a stop just
  outside the minimum gap, clamped to the vehicle's deceleration limit.

The plant dynamics (vehicle model) live in the simulator; this module only
computes the acceleration command.
"""

from pid_controller import PIDController


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


class AdaptiveCruiseControl:
    def __init__(self, config):
        acc = config['acc_settings']
        vehicle = config['vehicle']
        pid_speed = config.get('pid_speed', {'kp': 0.1, 'ki': 0.01, 'kd': 0.0})
        pid_distance = config.get('pid_distance',
                                  {'kp': 0.1, 'ki': 0.01, 'kd': 0.0})

        self.set_speed = float(acc['set_speed'])
        self.time_headway = float(acc['time_headway'])
        self.min_distance = float(acc['min_distance'])
        self.ttc_threshold = float(acc['emergency_ttc_threshold'])
        self.max_accel = float(vehicle['max_acceleration'])
        self.max_decel = float(vehicle['max_deceleration'])

        # Emergency braking brings the ego to a stop at this gap.
        self.stop_margin = 2.0
        self._stop_gap = self.min_distance + self.stop_margin

        self.speed_pid = PIDController(
            float(pid_speed['kp']), float(pid_speed['ki']),
            float(pid_speed['kd']),
            output_min=self.max_decel, output_max=self.max_accel,
            integral_min=-50.0, integral_max=50.0)
        self.distance_pid = PIDController(
            float(pid_distance['kp']), float(pid_distance['ki']),
            float(pid_distance['kd']),
            output_min=-8.0, output_max=8.0,
            integral_min=-20.0, integral_max=20.0)

        # Gap-closing limiter: the ego never approaches the lead faster
        # than a speed whose closing TTC stays well above the emergency
        # threshold (approach speed <= distance / 4  =>  TTC >= 4 s).
        self.max_approach_speed = 5.0

    def reset(self):
        self.speed_pid.reset()
        self.distance_pid.reset()

    def safe_following_distance(self, speed):
        return speed * self.time_headway + self.min_distance

    def time_to_collision(self, ego_speed, lead_speed, distance):
        relative = ego_speed - lead_speed
        if relative <= 0:
            return None
        return distance / relative

    def emergency_accel(self, ego_speed, distance):
        """Controlled constant-deceleration braking to a stop at the
        minimum gap: a = -v^2 / (2 * (d - stop_gap)), clamped to the
        vehicle limits."""
        gap = max(distance - self._stop_gap, 0.5)
        accel = -(ego_speed ** 2) / (2.0 * gap)
        return _clamp(accel, self.max_decel, -1.0)

    def compute(self, ego_speed, lead_speed, distance, dt):
        """Return (acceleration_cmd, mode, distance_error)."""
        if lead_speed is None:
            # Cruise: regulate ego speed to the set speed.
            error = self.set_speed - ego_speed
            accel = self.speed_pid.compute(error, dt)
            accel = _clamp(accel, self.max_decel, self.max_accel)
            return accel, 'cruise', None

        ttc = self.time_to_collision(ego_speed, lead_speed, distance)

        if ttc is not None and ttc < self.ttc_threshold:
            # Emergency: controlled maximum braking. PIDs are frozen to
            # avoid integral windup while the brake is engaged.
            return self.emergency_accel(ego_speed, distance), 'emergency', None

        # Follow: target the lead speed plus a gap-correction term from
        # the distance PID, limited so closing TTC never nears the
        # emergency threshold and never exceeding the set speed.
        distance_error = distance - self.safe_following_distance(ego_speed)
        increment = self.distance_pid.compute(distance_error, dt)
        approach_limit = min(self.max_approach_speed, distance / 4.0)
        increment = _clamp(increment, -8.0, approach_limit)
        desired_speed = _clamp(lead_speed + increment, 0.0, self.set_speed)

        accel = self.speed_pid.compute(desired_speed - ego_speed, dt)
        accel = _clamp(accel, self.max_decel, self.max_accel)
        return accel, 'follow', distance_error
