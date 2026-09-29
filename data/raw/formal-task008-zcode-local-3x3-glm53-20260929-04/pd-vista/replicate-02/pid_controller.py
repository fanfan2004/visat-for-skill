"""PID controller for the Adaptive Cruise Control simulation.

Textbook discrete PID:

    u(t) = kp * e(t) + ki * sum(e * dt) + kd * (e(t) - e(t-1)) / dt

The integral term is clamped to an anti-windup bound so a saturated actuator
(full throttle / full braking) cannot wind it up without bound.
"""


class PIDController:
    """Discrete PID controller.

    Parameters
    ----------
    kp : float   proportional gain
    ki : float   integral gain
    kd : float   derivative gain
    integral_limit : float, optional
        Anti-windup bound on the accumulated integral term (absolute value).
        Defaults to 1e6 (effectively unbounded).
    """

    def __init__(self, kp, ki, kd, integral_limit=1e6):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.integral_limit = integral_limit
        self.reset()

    def reset(self):
        """Clear the controller state (integral accumulator and previous error)."""
        self.integral = 0.0
        self.prev_error = 0.0
        self._first_call = True

    def compute(self, error, dt, out_min=None, out_max=None):
        """Compute the control output for the given error and timestep.

        Returns a float (e.g. an acceleration command).

        out_min / out_max optionally saturate the output and enable
        conditional anti-windup: while the output is saturated, the
        integrator only accumulates when the error pushes the output
        further into saturation.
        """
        if self._first_call:
            self.prev_error = error
            self._first_call = False
        derivative = (error - self.prev_error) / dt

        tentative = self.integral + error * dt
        if tentative > self.integral_limit:
            tentative = self.integral_limit
        elif tentative < -self.integral_limit:
            tentative = -self.integral_limit

        output = self.kp * error + self.ki * tentative + self.kd * derivative
        if out_max is not None and output > out_max and error > 0:
            tentative = self.integral  # actuator saturated: hold integrator
        elif out_min is not None and output < out_min and error < 0:
            tentative = self.integral
        self.integral = tentative

        output = self.kp * error + self.ki * self.integral + self.kd * derivative
        if out_max is not None:
            output = min(output, out_max)
        if out_min is not None:
            output = max(output, out_min)
        self.prev_error = error
        return output
