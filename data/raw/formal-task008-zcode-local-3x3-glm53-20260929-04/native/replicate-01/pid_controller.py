"""Discrete-time PID controller for the adaptive cruise control task.

Implements the standard PID control law

    output = Kp * error + Ki * integral(error) + Kd * derivative(error)

with optional output clamping, integral clamping and conditional-integration
anti-windup so the controller behaves well when the actuator saturates
(e.g. during the 0 -> 30 m/s speed ramp with the +/- acceleration limits).
"""


class PIDController:
    """PID controller with anti-windup.

    Args:
        kp: proportional gain.
        ki: integral gain.
        kd: derivative gain.
        output_min: optional lower clamp on the output (e.g. max deceleration).
        output_max: optional upper clamp on the output (e.g. max acceleration).
        integral_limit: optional clamp on the accumulated integral magnitude.
    """

    def __init__(self, kp, ki, kd, output_min=None, output_max=None,
                 integral_limit=None):
        self.kp = float(kp)
        self.ki = float(ki)
        self.kd = float(kd)
        self.output_min = output_min
        self.output_max = output_max
        self.integral_limit = integral_limit
        self.integral = 0.0
        self.prev_error = 0.0

    def reset(self):
        """Clear controller state (integral and previous error)."""
        self.integral = 0.0
        self.prev_error = 0.0

    def compute(self, error, dt):
        """Compute the control output for the current error and timestep.

        Args:
            error: setpoint - measured value.
            dt: timestep in seconds (must be > 0 for the derivative term).

        Returns:
            Control output as a float.
        """
        # Proportional term
        p_term = self.kp * error

        # Derivative term
        derivative = (error - self.prev_error) / dt if dt > 0 else 0.0
        d_term = self.kd * derivative

        # Trial integral update with optional integral clamping
        new_integral = self.integral + error * dt
        if self.integral_limit is not None:
            new_integral = max(-self.integral_limit,
                               min(self.integral_limit, new_integral))
        i_term = self.ki * new_integral

        output = p_term + i_term + d_term

        # Conditional-integration anti-windup: while the output is saturated,
        # stop accumulating error that would push the output further into
        # saturation. This prevents integral windup during long saturated
        # phases such as the initial full-throttle speed ramp.
        saturated_high = (self.output_max is not None
                          and output > self.output_max and error > 0)
        saturated_low = (self.output_min is not None
                         and output < self.output_min and error < 0)
        if saturated_high or saturated_low:
            new_integral = self.integral  # keep the old integral
            i_term = self.ki * new_integral
            output = p_term + i_term + d_term

        self.integral = new_integral
        self.prev_error = error

        # Output clamping
        if self.output_min is not None:
            output = max(output, self.output_min)
        if self.output_max is not None:
            output = min(output, self.output_max)

        return float(output)
