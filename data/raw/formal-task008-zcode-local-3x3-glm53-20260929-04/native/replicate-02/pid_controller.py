"""PID controller for the Adaptive Cruise Control simulation.

Implements a discrete-time PID controller with conditional-integration
anti-windup and optional output clamping, following the control law:

    output = Kp * error + Ki * integral(error) + Kd * derivative(error)
"""


class PIDController:
    def __init__(self, kp, ki, kd, output_min=None, output_max=None,
                 integral_min=None, integral_max=None):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.output_min = output_min
        self.output_max = output_max
        self.integral_min = integral_min
        self.integral_max = integral_max
        self.integral = 0.0
        self.prev_error = 0.0

    def reset(self):
        """Clear controller state (integral and previous error)."""
        self.integral = 0.0
        self.prev_error = 0.0

    def compute(self, error, dt):
        """Compute the control output for the given error and timestep."""
        # Proportional term
        p_term = self.kp * error

        # Derivative term (on error difference, guard dt <= 0)
        derivative = (error - self.prev_error) / dt if dt > 0 else 0.0
        d_term = self.kd * derivative
        self.prev_error = error

        # Integral term with conditional-integration anti-windup:
        # do not accumulate further while the output is saturated in the
        # direction the error would push it.
        candidate = self.integral + error * dt
        i_term = self.ki * candidate
        output = p_term + i_term + d_term

        saturated_high = (self.output_max is not None
                          and output >= self.output_max and error > 0)
        saturated_low = (self.output_min is not None
                         and output <= self.output_min and error < 0)
        if not saturated_high and not saturated_low:
            self.integral = candidate
            if self.integral_max is not None:
                self.integral = min(self.integral, self.integral_max)
            if self.integral_min is not None:
                self.integral = max(self.integral, self.integral_min)
            i_term = self.ki * self.integral
            output = p_term + i_term + d_term

        # Output clamping
        if self.output_max is not None:
            output = min(output, self.output_max)
        if self.output_min is not None:
            output = max(output, self.output_min)

        return float(output)
