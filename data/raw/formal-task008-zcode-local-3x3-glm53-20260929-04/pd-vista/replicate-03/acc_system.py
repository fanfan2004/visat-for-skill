"""Adaptive Cruise Control (ACC) system.

Architecture (cascade controller)
---------------------------------
* ``cruise``   : no lead vehicle detected -> the speed PID drives the ego
  toward the set speed.
* ``follow``   : a lead vehicle is present and TTC is safe -> a cascade of two
  loops. The outer distance PID converts the gap error into a speed-reference
  correction relative to the lead vehicle (``speed_ref = lead - pid_out``), so a
  too-small gap commands a lower reference (open the gap) while a too-large gap
  commands a higher reference (close the gap). The reference is clamped to
  ``[0, set_speed]`` so ACC never accelerates beyond the set speed and never
  commands a closing rate that grows without bound. The inner speed PID then
  converts the reference error into an acceleration command.
* ``emergency``: TTC below the threshold -> full-brake command (max
  deceleration) regardless of the PIDs.

All acceleration commands are clamped to the vehicle limits
``[max_deceleration, max_acceleration]``.
"""

from pid_controller import PIDController


class AdaptiveCruiseControl:
    CRUISE = "cruise"
    FOLLOW = "follow"
    EMERGENCY = "emergency"

    def __init__(self, config):
        vehicle = config.get("vehicle", {})
        acc = config.get("acc_settings", {})

        self.lead_filter_alpha = float(acc.get("lead_filter_alpha", 0.5))
        self.mass = vehicle.get("mass", 1500)
        self.max_acceleration = float(vehicle.get("max_acceleration", 3.0))
        self.max_deceleration = float(vehicle.get("max_deceleration", -8.0))

        self.set_speed = float(acc.get("set_speed", 30.0))
        self.time_headway = float(acc.get("time_headway", 1.5))
        self.min_distance = float(acc.get("min_distance", 10.0))
        self.emergency_ttc_threshold = float(acc.get("emergency_ttc_threshold", 3.0))

        speed_gains = config.get("pid_speed", {}) or {}
        dist_gains = config.get("pid_distance", {}) or {}

        # Anti-windup: the speed PID knows the acceleration limits so it stops
        # integrating while saturated; the distance PID output is a speed
        # correction, clamped to a sane range for the same reason.
        self.speed_pid = PIDController(
            speed_gains.get("kp", 0.5),
            speed_gains.get("ki", 0.0),
            speed_gains.get("kd", 0.0),
            integral_limit=1.0,
            output_limits=(self.max_deceleration, self.max_acceleration),
        )
        self.dist_pid = PIDController(
            dist_gains.get("kp", 0.2),
            dist_gains.get("ki", 0.0),
            dist_gains.get("kd", 0.0),
            integral_limit=2.0,
            output_limits=(-2.0, 2.0),
        )

        self.mode = None
        self._mode = None
        self.ttc = None
        self._lead_ema = None

    def desired_distance(self, ego_speed):
        """Safe following distance policy: minimum gap plus headway term."""
        return self.min_distance + self.time_headway * ego_speed

    def compute_ttc(self, ego_speed, lead_speed, distance):
        """Time-to-collision; ``None`` when the ego is not approaching."""
        closing = float(ego_speed) - float(lead_speed)
        if closing <= 0:
            return None
        return float(distance) / closing

    def compute(self, ego_speed, lead_speed, distance, dt, sensor_distance=None):
        """Return ``(acceleration_cmd, mode, distance_error)`` for one step.

        ``distance`` is the gap used for gap control (the fused estimate);
        ``distance_error`` is ``desired_distance - distance`` (positive when
        the ego is too close) and is ``None`` in cruise mode where no gap
        exists. ``sensor_distance`` is the optional raw sensor-measured gap,
        used for cut-in detection in the TTC safety layer.
        """
        ego_speed = float(ego_speed)

        if lead_speed is None or distance is None:
            # ---- cruise: no lead vehicle, track the set speed ----------------
            mode = self.CRUISE
            distance_error = None
            self.ttc = None
            if self._mode != mode:
                self._reset_controllers()
            raw = self.speed_pid.compute(self.set_speed - ego_speed, dt)
        else:
            desired = self.desired_distance(ego_speed)
            distance_error = desired - float(distance)

            # TTC from the control gap and, when supplied, from the raw
            # sensor-measured gap. A sudden cut-in shows up in the sensor
            # reading long before the fused gap would, so the safety layer
            # considers both. TTC is None when the ego is not approaching.
            ttcs = [self.compute_ttc(ego_speed, float(lead_speed), float(distance))]
            if sensor_distance is not None:
                ttcs.append(self.compute_ttc(ego_speed, float(lead_speed), float(sensor_distance)))
            active = [t for t in ttcs if t is not None]
            self.ttc = min(active) if active else None

            if self.ttc is not None and self.ttc < self.emergency_ttc_threshold:
                # ---- emergency: imminent collision, full braking -------------
                mode = self.EMERGENCY
                if self._mode != mode:
                    self._reset_controllers()
                raw = self.max_deceleration
            else:
                # ---- follow: cascade distance -> speed -> acceleration -------
                mode = self.FOLLOW
                if self._mode != mode:
                    self._reset_controllers()
                # Light EMA filter on the lead speed for the reference law; the
                # raw lead speed is still used for the TTC safety check above.
                lead = float(lead_speed)
                if self._lead_ema is None:
                    self._lead_ema = lead
                else:
                    self._lead_ema += self.lead_filter_alpha * (lead - self._lead_ema)
                # Clamp the gap error seen by the outer loop so a far lead can
                # only be chased at a bounded rate and a large surplus gap does
                # not produce an unbounded speed correction.
                err_span = self.min_distance
                err_c = max(-err_span, min(err_span, distance_error))
                speed_ref = self._lead_ema - self.dist_pid.compute(err_c, dt)
                speed_ref = max(0.0, min(self.set_speed, speed_ref))
                raw = self.speed_pid.compute(speed_ref - ego_speed, dt)

        accel = max(self.max_deceleration, min(self.max_acceleration, raw))
        self.mode = mode
        self._mode = mode
        return accel, mode, distance_error

    def _reset_controllers(self):
        """Clear integrator/derivative state when the control mode changes."""
        self.speed_pid.reset()
        self.dist_pid.reset()
        self._lead_ema = None
