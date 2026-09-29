"""Adaptive Cruise Control (ACC) controller.

The controller runs at a fixed sample time and selects one of three modes:

* ``cruise``    - no lead vehicle reported by the sensor; the speed PID
                  regulates the ego speed toward ``acc_settings.set_speed``.
* ``follow``    - a lead vehicle is present and TTC is above the emergency
                  threshold; the distance PID regulates the measured gap to
                  the desired headway
                  ``min_distance + time_headway * ego_speed``.
* ``emergency`` - TTC has dropped below ``emergency_ttc_threshold``; the
                  controller commands maximum deceleration.

The ``distance_error`` convention: ``actual_distance - desired_distance``,
i.e. positive means the ego is too far from the lead (accelerate), negative
means too close (brake). In cruise mode no lead is tracked and the error is
``None``.

The gap between the vehicles is a plant/measurement quantity; the
``distance`` argument is whatever the sensor/simulation reports for the
current step.
"""

from pid_controller import PIDController


class AdaptiveCruiseControl:
    def __init__(self, config):
        """
        Args:
            config: nested configuration dict loaded from vehicle_params.yaml,
                e.g. config['acc_settings']['set_speed'],
                config['vehicle']['max_acceleration'],
                config['pid_speed'], config['pid_distance'],
                config['simulation']['dt'].
        """
        vehicle = config.get('vehicle', {})
        acc = config.get('acc_settings', {})
        sim = config.get('simulation', {})

        self.set_speed = float(acc.get('set_speed', 30.0))
        self.time_headway = float(acc.get('time_headway', 1.5))
        self.min_distance = float(acc.get('min_distance', 10.0))
        self.emergency_ttc_threshold = float(acc.get('emergency_ttc_threshold', 3.0))
        self.max_acceleration = float(vehicle.get('max_acceleration', 3.0))
        self.max_deceleration = float(vehicle.get('max_deceleration', -8.0))
        self.dt = float(sim.get('dt', 0.1))

        # Output limits = the physical acceleration envelope; giving them to
        # the PIDs enables conditional-integration anti-windup.
        accel_limits = (self.max_deceleration, self.max_acceleration)

        speed_gains = config.get('pid_speed', {})
        distance_gains = config.get('pid_distance', {})
        # The distance error carries speed noise (the lead speed feeds the gap
        # derivative), so its derivative term is lightly low-pass filtered.
        self.speed_pid = PIDController(
            speed_gains.get('kp', 0.1),
            speed_gains.get('ki', 0.01),
            speed_gains.get('kd', 0.0),
            output_limits=accel_limits,
        )
        self.distance_pid = PIDController(
            distance_gains.get('kp', 0.1),
            distance_gains.get('ki', 0.01),
            distance_gains.get('kd', 0.0),
            output_limits=accel_limits,
            d_filter_alpha=0.4,
        )
        self._prev_mode = None

    def reset(self):
        self.speed_pid.reset()
        self.distance_pid.reset()
        self._prev_mode = None

    def desired_distance(self, ego_speed):
        """Constant-time-headway spacing law: standstill gap plus headway term.

        The offset form guarantees the commanded spacing never falls below
        min_distance while tracking the 1.5 s headway at speed.
        """
        return self.min_distance + self.time_headway * ego_speed

    def _on_mode_change(self, mode):
        # Avoid stale derivative state (derivative kick) when a PID becomes
        # active again after being idle in another mode.
        if mode != self._prev_mode:
            if mode == 'cruise':
                self.speed_pid.reset()
            elif mode in ('follow', 'emergency'):
                self.distance_pid.reset()
            self._prev_mode = mode

    def compute(self, ego_speed, lead_speed, distance, dt):
        """Compute one ACC control step.

        Args:
            ego_speed: current ego speed in m/s.
            lead_speed: lead vehicle speed in m/s, or None when no lead is
                detected.
            distance: current gap to the lead in meters (None when no lead is
                detected).
            dt: control time step in seconds.

        Returns:
            (acceleration_cmd, mode, distance_error) tuple. acceleration_cmd
            is in m/s^2 (clamped to the vehicle acceleration envelope), mode
            is one of 'cruise' / 'follow' / 'emergency', distance_error is
            actual - desired gap in meters (None when no lead is tracked).
        """
        if lead_speed is None or distance is None:
            mode = 'cruise'
            self._on_mode_change(mode)
            distance_error = None
            speed_error = self.set_speed - ego_speed
            acceleration_cmd = self.speed_pid.compute(speed_error, dt)
        else:
            distance = float(distance)
            ego_speed = float(ego_speed)
            lead_speed = float(lead_speed)
            desired = self.desired_distance(ego_speed)
            distance_error = distance - desired
            closing = ego_speed - lead_speed
            if closing > 0:
                ttc = distance / closing
            else:
                ttc = float('inf')

            if ttc < self.emergency_ttc_threshold or distance <= 0.0:
                mode = 'emergency'
                self._on_mode_change(mode)
                # Maximum braking, no modulation: TTC below the threshold
                # means the situation is already critical.
                acceleration_cmd = self.max_deceleration
            else:
                mode = 'follow'
                self._on_mode_change(mode)
                acceleration_cmd = self.distance_pid.compute(distance_error, dt)
                # Guaranteed-stopping guard: brake at least hard enough to
                # cancel the closing speed by the time the gap shrinks to
                # min_distance (with a 1.5x safety factor), so the minimum-gap
                # constraint holds even if the PID reacts too slowly.
                closing = max(0.0, ego_speed - lead_speed)
                margin = distance - self.min_distance
                if closing > 0.0:
                    a_stop = 1.5 * closing * closing / (2.0 * max(margin, 0.1))
                    if -a_stop < acceleration_cmd:
                        acceleration_cmd = max(self.max_deceleration, -a_stop)
                # Never push past the driver's set speed to close a gap.
                if ego_speed >= self.set_speed and acceleration_cmd > 0:
                    acceleration_cmd = 0.0
                # Already stopped: do not command further deceleration.
                if ego_speed <= 0.0 and acceleration_cmd < 0:
                    acceleration_cmd = 0.0

        acceleration_cmd = max(self.max_deceleration,
                               min(self.max_acceleration, acceleration_cmd))
        return acceleration_cmd, mode, distance_error
