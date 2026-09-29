"""Adaptive Cruise Control (ACC) system.

Architecture
------------
The controller runs one of three modes each step:

* ``cruise``    - no lead vehicle detected: a speed PID regulates the ego
                  speed towards the set speed.
* ``follow``    - a lead vehicle is detected: a distance PID (outer loop)
                  converts the spacing error into a speed offset around the
                  lead speed, capped by the set speed; the speed PID (inner
                  loop) then tracks that reference speed.
* ``emergency`` - time-to-collision (TTC) below the emergency threshold:
                  command the braking deceleration required to bring the
                  relative speed to zero inside the minimum safety gap.

Safety features: acceleration/deceleration limits, anti-windup on both PIDs,
a hard minimum-gap headway policy (10 m + 1.5 s time headway) and TTC-based
emergency braking.
"""

import math

from pid_controller import PIDController

# Cap on how far the follow-mode reference speed may deviate from the lead
# speed (m/s). Keeps the approach to a far lead gradual and bounds the
# authority of the distance loop.
SPEED_OFFSET_LIMIT = 10.0

# Exponential smoothing coefficient for the noisy lead-speed measurement used
# by the follow-mode control law (tau ~ 0.6 s at dt = 0.1 s). Safety logic
# (TTC) still uses the raw measurement.
LEAD_FILTER_ALPHA = 0.15


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(value)


class AdaptiveCruiseControl:
    """ACC controller built from the nested configuration of vehicle_params.yaml.

    Parameters
    ----------
    config : dict
        Nested dict, e.g. ``config['acc_settings']['set_speed']``,
        ``config['vehicle']['max_acceleration']``,
        ``config['pid_speed']['kp']``.
    """

    def __init__(self, config):
        acc = config['acc_settings']
        vehicle = config['vehicle']

        self.set_speed = float(acc['set_speed'])
        self.time_headway = float(acc['time_headway'])
        self.min_distance = float(acc['min_distance'])
        self.ttc_threshold = float(acc['emergency_ttc_threshold'])

        self.max_acceleration = float(vehicle['max_acceleration'])
        self.max_deceleration = float(vehicle['max_deceleration'])

        ps = config['pid_speed']
        pd = config['pid_distance']
        self.speed_pid = PIDController(
            ps['kp'], ps['ki'], ps['kd'],
            output_limits=(self.max_deceleration, self.max_acceleration))
        self.distance_pid = PIDController(
            pd['kp'], pd['ki'], pd['kd'],
            output_limits=(-SPEED_OFFSET_LIMIT, SPEED_OFFSET_LIMIT))

        self.mode = 'cruise'
        self._lead_ema = None   # smoothed lead speed for the control law

    def desired_distance(self, ego_speed):
        """Safety-critical headway policy: minimum gap + time-headway term."""
        return self.min_distance + self.time_headway * max(ego_speed, 0.0)

    def compute(self, ego_speed, lead_speed, distance, dt):
        """Compute one ACC control step.

        Parameters
        ----------
        ego_speed : float
            Current ego vehicle speed (m/s).
        lead_speed : float or None
            Lead vehicle speed (m/s); None when no lead is detected.
        distance : float or None
            Gap to the lead vehicle (m); None when no lead is detected.
        dt : float
            Control time step (s).

        Returns
        -------
        tuple (acceleration_cmd, mode, distance_error)
            acceleration_cmd : float in [max_deceleration, max_acceleration]
            mode : 'cruise' | 'follow' | 'emergency'
            distance_error : desired_distance - distance (None in cruise mode)
        """
        if lead_speed is None or not _is_number(lead_speed):
            # --- cruise: regulate speed only ---
            self.mode = 'cruise'
            self._lead_ema = None
            speed_error = self.set_speed - ego_speed
            accel = self.speed_pid.compute(speed_error, dt)
            accel = max(self.max_deceleration, min(self.max_acceleration, accel))
            return accel, self.mode, None

        # --- lead detected: evaluate spacing and TTC ---
        if distance is None or not _is_number(distance):
            distance = self.min_distance
        distance_error = self.desired_distance(ego_speed) - distance
        closing_speed = ego_speed - lead_speed
        if closing_speed > 1e-6:
            ttc = distance / closing_speed
        else:
            ttc = float('inf')

        if ttc < self.ttc_threshold:
            # --- emergency: brake to stop the closing speed inside the gap ---
            self.mode = 'emergency'
            stop_gap = max(distance - self.min_distance, 1.0)
            required_decel = closing_speed ** 2 / (2.0 * stop_gap)
            accel = -required_decel
            if distance <= self.min_distance:
                # Already inside the minimum gap: guarantee firm braking.
                accel = min(accel, -2.0)
            accel = max(self.max_deceleration, min(0.0, accel))
        else:
            # --- follow: cascade of distance loop -> speed loop ---
            self.mode = 'follow'
            # Low-pass the noisy lead-speed measurement for the control law;
            # the raw measurement is kept for the TTC safety logic above.
            if self._lead_ema is None:
                self._lead_ema = lead_speed
            else:
                a = LEAD_FILTER_ALPHA
                self._lead_ema = self._lead_ema + a * (lead_speed - self._lead_ema)
            # Spacing error is positive when too close; the distance PID
            # output is subtracted from the lead speed to give a reference
            # speed below (too close) or above (too far) the lead speed.
            speed_offset = -self.distance_pid.compute(distance_error, dt)
            v_ref = min(self.set_speed, self._lead_ema + speed_offset)
            speed_error = v_ref - ego_speed
            accel = self.speed_pid.compute(speed_error, dt)
            accel = max(self.max_deceleration, min(self.max_acceleration, accel))

        return accel, self.mode, distance_error
