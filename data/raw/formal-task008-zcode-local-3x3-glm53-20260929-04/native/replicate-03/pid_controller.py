"""PID controller for the adaptive cruise control system.

Implements the discrete-time PID control law:

    output = Kp * error + Ki * integral(error) + Kd * derivative(error)

with optional output clamping and conditional-integration anti-windup.
"""


class PIDController:
    """Discrete-time PID controller.

    Args:
        kp: Proportional gain.
        ki: Integral gain.
        kd: Derivative gain.
        output_min: Optional lower clamp for the output.
        output_max: Optional upper clamp for the output.
    """

    def __init__(self, kp, ki, kd, output_min=None, output_max=None):
        self.kp = float(kp)
        self.ki = float(ki)
        self.kd = float(kd)
        self.output_min = output_min
        self.output_max = output_max
        self.integral = 0.0
        self.prev_error = 0.0

    def reset(self):
        """Clear controller state (integral and previous error)."""
        self.integral = 0.0
        self.prev_error = 0.0

    def compute(self, error, dt):
        """Compute the control output for the current error and timestep.

        Args:
            error: Setpoint minus measured value.
            dt: Timestep in seconds (must be > 0 for the derivative term).

        Returns:
            Control output as a float.
        """
        p_term = self.kp * error
        derivative = (error - self.prev_error) / dt if dt > 0 else 0.0
        d_term = self.kd * derivative

        tentative_integral = self.integral + error * dt
        output = p_term + self.ki * tentative_integral + d_term

        # Conditional-integration anti-windup: while the output is clamped,
        # stop accumulating integral that would push it further into
        # saturation. Without output limits this never triggers and the
        # controller follows the plain PID law exactly.
        at_max = self.output_max is not None and output > self.output_max and error > 0
        at_min = self.output_min is not None and output < self.output_min and error < 0
        if not (at_max or at_min):
            self.integral = tentative_integral

        i_term = self.ki * self.integral
        output = p_term + i_term + d_term

        if self.output_min is not None:
            output = max(output, self.output_min)
        if self.output_max is not None:
            output = min(output, self.output_max)

        self.prev_error = error
        return float(output)
