"""Generic discrete PID controller.

Implements the standard PID law

    u(t) = kp * e(t) + ki * integral(e) + kd * de/dt

with two practical extensions that matter for vehicle control:

* optional output limits ``(low, high)`` so the controller can be made aware
  of the actuator range it feeds (acceleration commands),
* conditional-integration anti-windup: while the output is saturated, the
  integrator is frozen whenever accumulating would push the output further
  past the limit.  This prevents large integrator wind-up during long
  saturation phases (e.g. full-threshold acceleration from standstill),
  which would otherwise cause big overshoot when the setpoint is reached.
"""


class PIDController:
    """Discrete PID controller with anti-windup and optional output limits."""

    def __init__(self, kp, ki, kd, out_limits=None):
        self.kp = float(kp)
        self.ki = float(ki)
        self.kd = float(kd)
        self.out_limits = out_limits  # (low, high) or None
        self.reset()

    def reset(self):
        """Clear integrator and derivative memory."""
        self.integral = 0.0
        self.prev_error = None
        self.prev_output = 0.0

    def compute(self, error, dt):
        """Advance one control step.

        error : control error at the current step
        dt    : timestep in seconds (must be > 0)
        returns the control output as a float
        """
        if self.prev_error is None:
            derivative = 0.0
        else:
            derivative = (error - self.prev_error) / dt
        self.prev_error = error

        integral_try = self.integral + error * dt
        unsaturated = self.kp * error + self.ki * integral_try + self.kd * derivative

        if self.out_limits is not None:
            low, high = self.out_limits
            saturated_high = unsaturated > high
            saturated_low = unsaturated < low
            if (saturated_high and error > 0) or (saturated_low and error < 0):
                # freeze the integrator: accumulating would deepen saturation
                integral_try = self.integral
            output = min(high, max(low, unsaturated))
        else:
            output = unsaturated

        self.integral = integral_try
        self.prev_output = output
        return float(output)
