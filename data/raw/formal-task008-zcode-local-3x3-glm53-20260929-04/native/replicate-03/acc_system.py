"""Adaptive Cruise Control (ACC) system.

Mode selection:
    - 'cruise':    no lead vehicle detected -> regulate speed to set_speed
    - 'emergency': lead present and TTC below the emergency threshold
                   -> kinematic emergency braking
    - 'follow':    lead present, safe TTC -> regulate the following gap

Control structure:
    - cruise:    PID on speed error (set_speed - ego_speed) -> acceleration
    - follow:    PID on distance error (distance - desired_distance) -> acceleration,
                 where desired_distance uses the time-headway model with the
                 min_distance standstill margin
                 (ego_speed * time_headway + min_distance). The ego vehicle never
                 accelerates beyond set_speed while following.
    - emergency: bypasses the PID loops and commands maximum braking
                 (max_deceleration) until TTC recovers.
"""

from pid_controller import PIDController

# Default gains used only when the config dict carries no pid sections.
_DEFAULT_SPEED_GAINS = {'kp': 0.5, 'ki': 0.0, 'kd': 0.1}
_DEFAULT_DISTANCE_GAINS = {'kp': 0.3, 'ki': 0.02, 'kd': 0.0}


class AdaptiveCruiseControl:
    """ACC controller built from the nested vehicle_params.yaml config dict.

    Args:
        config: Nested dict, e.g. config['acc_settings']['set_speed'].
                Optional 'pid_speed' / 'pid_distance' sections supply the
                default PID gains; simulation.py overrides them with the
                tuned gains from tuning_results.yaml via set_pid_gains().
    """

    def __init__(self, config):
        vehicle = config['vehicle']
        acc = config['acc_settings']
        self.set_speed = float(acc['set_speed'])
        self.time_headway = float(acc['time_headway'])
        self.min_distance = float(acc['min_distance'])
        self.ttc_threshold = float(acc['emergency_ttc_threshold'])
        self.max_accel = float(vehicle['max_acceleration'])
        self.max_decel = float(vehicle['max_deceleration'])

        speed_gains = config.get('pid_speed', _DEFAULT_SPEED_GAINS)
        distance_gains = config.get('pid_distance', _DEFAULT_DISTANCE_GAINS)
        self.speed_pid = PIDController(
            speed_gains['kp'], speed_gains['ki'], speed_gains['kd'],
            output_min=self.max_decel, output_max=self.max_accel)
        self.distance_pid = PIDController(
            distance_gains['kp'], distance_gains['ki'], distance_gains['kd'],
            output_min=self.max_decel, output_max=self.max_accel)

    def set_pid_gains(self, speed_gains, distance_gains):
        """Replace the PID gains (e.g. with tuned values from tuning_results.yaml)."""
        self.speed_pid = PIDController(
            speed_gains['kp'], speed_gains['ki'], speed_gains['kd'],
            output_min=self.max_decel, output_max=self.max_accel)
        self.distance_pid = PIDController(
            distance_gains['kp'], distance_gains['ki'], distance_gains['kd'],
            output_min=self.max_decel, output_max=self.max_accel)

    def reset(self):
        """Reset both PID controllers."""
        self.speed_pid.reset()
        self.distance_pid.reset()

    def desired_distance(self, ego_speed):
        """Safe following distance from the time-headway model (meters):
        ego_speed * time_headway + min_distance standstill margin."""
        return ego_speed * self.time_headway + self.min_distance

    def time_to_collision(self, ego_speed, lead_speed, distance):
        """TTC in seconds, or None when not approaching (or no valid gap)."""
        if distance is None:
            return None
        relative_speed = ego_speed - lead_speed
        if relative_speed <= 0 or distance <= 0:
            return None
        return distance / relative_speed

    def compute(self, ego_speed, lead_speed, distance, dt):
        """Compute one ACC control step.

        Args:
            ego_speed: Current ego speed (m/s).
            lead_speed: Lead vehicle speed (m/s) or None when no lead detected.
            distance: Measured gap to the lead vehicle (m) or None.
            dt: Timestep in seconds.

        Returns:
            Tuple (acceleration_cmd, mode, distance_error).
            distance_error is None in cruise mode (no lead vehicle).
        """
        if lead_speed is None:
            error = self.set_speed - ego_speed
            accel = self.speed_pid.compute(error, dt)
            return float(accel), 'cruise', None

        if distance is None:
            # Lead detected but no valid range measurement: fall back to
            # matching the lead speed (capped at set_speed) without a gap loop.
            target_speed = min(self.set_speed, lead_speed)
            accel = self.speed_pid.compute(target_speed - ego_speed, dt)
            return float(accel), 'follow', None

        desired = self.desired_distance(ego_speed)
        distance_error = distance - desired
        ttc = self.time_to_collision(ego_speed, lead_speed, distance)

        if ttc is not None and ttc < self.ttc_threshold:
            # Emergency braking: maximum deceleration until TTC recovers
            # (robust to a lead vehicle braking to a standstill).
            accel = self.max_decel
            return float(accel), 'emergency', float(distance_error)

        accel = self.distance_pid.compute(distance_error, dt)
        # Never chase a faster lead beyond the set speed while following:
        # limit the command so ego_speed cannot cross set_speed this step.
        if dt > 0:
            headroom = (self.set_speed - ego_speed) / dt
            accel = min(accel, max(0.0, headroom))
        accel = max(self.max_decel, min(accel, self.max_accel))
        return float(accel), 'follow', float(distance_error)
