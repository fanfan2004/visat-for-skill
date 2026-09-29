"""Adaptive Cruise Control (ACC) system.

Mode selection per control step:
  * 'cruise'    - no lead vehicle detected: hold the set speed with the
                  speed PID.
  * 'emergency' - a lead vehicle is present and time-to-collision (TTC) is
                  below the emergency threshold: apply maximum braking.
  * 'follow'    - a lead vehicle is present and TTC is safe: regulate the
                  gap with the distance PID.  The commanded acceleration is
                  the more braking of the distance-PID output and a
                  speed-PID output that keeps the vehicle from exceeding
                  the set speed.

The desired following gap is  min_distance + time_headway * ego_speed.
The measured gap is low-pass filtered before feeding the distance PID so
sensor noise does not chatter the actuator; TTC and the reported
distance_error use the raw measurement.
"""

from pid_controller import PIDController

# Lead-vehicle position is only meaningful relative to the ego vehicle, so
# this module works purely on the measured gap.
_GOTO_MODES = ('cruise', 'follow', 'emergency')


class AdaptiveCruiseControl:
    def __init__(self, config):
        vehicle = config['vehicle']
        acc = config['acc_settings']
        self.set_speed = float(acc['set_speed'])
        self.time_headway = float(acc['time_headway'])
        self.min_distance = float(acc['min_distance'])
        self.ttc_threshold = float(acc['emergency_ttc_threshold'])
        self.max_accel = float(vehicle['max_acceleration'])
        self.max_decel = float(vehicle['max_deceleration'])

        speed_gains = config.get('pid_speed', {'kp': 0.1, 'ki': 0.01, 'kd': 0.0})
        dist_gains = config.get('pid_distance', {'kp': 0.1, 'ki': 0.01, 'kd': 0.0})
        self.speed_pid = PIDController(float(speed_gains['kp']),
                                       float(speed_gains['ki']),
                                       float(speed_gains['kd']))
        self.dist_pid = PIDController(float(dist_gains['kp']),
                                      float(dist_gains['ki']),
                                      float(dist_gains['kd']))

        # Low-pass time constant (s) for the gap measurement used by the
        # distance PID.
        self.gap_filter_tau = 0.6

        self.mode = 'cruise'
        self._gap_filtered = None

    def reset(self):
        self.speed_pid.reset()
        self.dist_pid.reset()
        self.mode = 'cruise'
        self._gap_filtered = None

    def compute(self, ego_speed, lead_speed, distance, dt):
        """Return (acceleration_cmd, mode, distance_error) for one step.

        lead_speed is None when no lead vehicle is detected.  distance_error
        is None in cruise mode.
        """
        if lead_speed is None:
            if self.mode != 'cruise':
                self.mode = 'cruise'
                self._gap_filtered = None
                self.dist_pid.reset()
                self.speed_pid.reset()
            error = self.set_speed - ego_speed
            a = self.speed_pid.compute(error, dt, self.max_decel, self.max_accel)
            return a, 'cruise', None

        desired = self.min_distance + self.time_headway * ego_speed
        distance_error = distance - desired
        closing = ego_speed - lead_speed
        ttc = distance / closing if closing > 1e-6 else float('inf')

        if self.mode == 'emergency':
            if ttc < self.ttc_threshold and closing > 0:
                return self.max_decel, 'emergency', distance_error
            # TTC recovered: hand control back to the gap regulator.
            self.mode = 'follow'
            self.dist_pid.reset()
            self._gap_filtered = distance

        if ttc < self.ttc_threshold and closing > 0:
            self.mode = 'emergency'
            self.dist_pid.reset()
            self._gap_filtered = None
            return self.max_decel, 'emergency', distance_error

        if self.mode != 'follow':
            self.mode = 'follow'
            self.dist_pid.reset()
            self._gap_filtered = distance

        alpha = dt / (self.gap_filter_tau + dt)
        if self._gap_filtered is None:
            self._gap_filtered = distance
        self._gap_filtered += alpha * (distance - self._gap_filtered)

        a_dist = self.dist_pid.compute(self._gap_filtered - desired, dt,
                                       self.max_decel, self.max_accel)
        a_speed = self.speed_pid.compute(self.set_speed - ego_speed, dt,
                                         self.max_decel, self.max_accel)
        a = min(a_dist, a_speed)
        a = max(self.max_decel, min(self.max_accel, a))
        return a, 'follow', distance_error
