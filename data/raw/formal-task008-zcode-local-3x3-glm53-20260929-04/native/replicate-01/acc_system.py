"""Adaptive Cruise Control (ACC) system.

Implements a cascaded ACC controller with three operating modes:

* ``cruise``   - no lead vehicle detected: regulate speed to the set speed
                 with the speed PID.
* ``follow``   - lead vehicle present: maintain a safe time-headway distance
                 (``speed * time_headway + min_distance``). The distance PID
                 (outer loop) converts the gap error into a speed correction
                 applied on top of the lead vehicle speed; the speed PID
                 (inner loop) converts the resulting speed reference into an
                 acceleration command.
* ``emergency``- time-to-collision below the threshold: full braking at the
                 vehicle's maximum deceleration.

All acceleration commands are clamped to the vehicle's physical limits
``[max_deceleration, max_acceleration]``.
"""

from pid_controller import PIDController


class AdaptiveCruiseControl:
    """ACC controller driven by the nested config from vehicle_params.yaml.

    Args:
        config: nested dict, e.g. ``config['acc_settings']['set_speed']``.
    """

    def __init__(self, config):
        acc = config['acc_settings']
        self.set_speed = float(acc['set_speed'])
        self.time_headway = float(acc['time_headway'])
        self.min_distance = float(acc['min_distance'])
        self.ttc_threshold = float(acc['emergency_ttc_threshold'])

        vehicle = config.get('vehicle', {})
        self.max_accel = float(vehicle.get('max_acceleration', 3.0))
        self.max_decel = float(vehicle.get('max_deceleration', -8.0))

        sim = config.get('simulation', {})
        self.dt = float(sim.get('dt', 0.1))

        ps = config.get('pid_speed', {})
        pd_ = config.get('pid_distance', {})
        # Inner loop: speed error -> acceleration, clamped to physical limits.
        self.speed_pid = PIDController(
            ps.get('kp', 0.1), ps.get('ki', 0.01), ps.get('kd', 0.0),
            output_min=self.max_decel, output_max=self.max_accel)
        # Outer loop: distance error -> speed correction (m/s).
        self.distance_pid = PIDController(
            pd_.get('kp', 0.1), pd_.get('ki', 0.01), pd_.get('kd', 0.0),
            output_min=-10.0, output_max=10.0)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def safe_following_distance(self, speed):
        """Time-headway model: desired gap at the given ego speed."""
        return speed * self.time_headway + self.min_distance

    def time_to_collision(self, distance, ego_speed, lead_speed):
        """TTC in seconds, or None when not approaching the lead vehicle."""
        relative_speed = ego_speed - lead_speed
        if relative_speed <= 0 or distance <= 0:
            return None
        return distance / relative_speed

    def _clamp_accel(self, accel):
        return max(self.max_decel, min(self.max_accel, accel))

    # ------------------------------------------------------------------
    # Main control step
    # ------------------------------------------------------------------
    def compute(self, ego_speed, lead_speed, distance, dt):
        """Compute one ACC control step.

        Args:
            ego_speed: current ego speed (m/s).
            lead_speed: lead vehicle speed (m/s) or None if no lead detected.
            distance: current gap to the lead vehicle (m) or None.
            dt: timestep in seconds.

        Returns:
            Tuple ``(acceleration_cmd, mode, distance_error)`` where
            ``distance_error`` is ``distance - desired_distance`` (m), or None
            when no lead vehicle/distance is available.
        """
        ego_speed = float(ego_speed)

        # Mode selection -------------------------------------------------
        if lead_speed is None or distance is None:
            mode = 'cruise'
        else:
            ttc = self.time_to_collision(distance, ego_speed,
                                         float(lead_speed))
            if ttc is not None and ttc < self.ttc_threshold:
                mode = 'emergency'
            else:
                mode = 'follow'

        # Control law ----------------------------------------------------
        if mode == 'cruise':
            distance_error = None
            accel = self.speed_pid.compute(self.set_speed - ego_speed, dt)
        else:
            desired_distance = self.safe_following_distance(ego_speed)
            distance_error = distance - desired_distance

            if mode == 'emergency':
                # Full braking at the physical deceleration limit.
                accel = self.max_decel
            else:  # follow
                # Outer loop: gap error -> speed correction on top of the
                # lead speed; reference capped at the set speed and at 0.
                gap_correction = self.distance_pid.compute(distance_error, dt)
                speed_ref = float(lead_speed) + gap_correction
                speed_ref_clamped = max(0.0, min(self.set_speed, speed_ref))
                if speed_ref_clamped != speed_ref and self.distance_pid.ki != 0:
                    # Back-calculation anti-windup: keep the integral
                    # consistent with the correction actually applied, so a
                    # long clamped phase (e.g. chasing a faster lead while
                    # capped at the set speed) cannot wind up the integrator
                    # and delay braking once the gap error turns negative.
                    applied = speed_ref_clamped - float(lead_speed)
                    self.distance_pid.integral += (applied - gap_correction) \
                        / self.distance_pid.ki
                speed_ref = speed_ref_clamped
                # Inner loop: speed error -> acceleration.
                accel = self.speed_pid.compute(speed_ref - ego_speed, dt)

        accel = self._clamp_accel(accel)
        return float(accel), mode, distance_error
