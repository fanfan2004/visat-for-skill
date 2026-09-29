"""PID controller for the Adaptive Cruise Control simulation.

Implements a discrete PID controller with optional output saturation and
conditional-integration anti-windup so the integral term does not wind up
while the command is saturated against the acceleration limits.
"""


class PIDController:
    """Discrete PID controller: u(t) = kp*e + ki*integral(e) + kd*de/dt.

    Parameters
    ----------
    kp, ki, kd : float
        Proportional, integral and derivative gains.
    output_limits : tuple (low, high), optional
        Symmetric saturation applied to the controller output. When the raw
        output leaves this band, integral accumulation is frozen in the
        direction that would deepen the saturation (anti-windup).
    """

    def __init__(self, kp, ki, kd, output_limits=None):
        self.kp = float(kp)
        self.ki = float(ki)
        self.kd = float(kd)
        self.output_limits = output_limits
        self.reset()

    def reset(self):
        """Clear integral, derivative memory and last output."""
        self.integral = 0.0
        self.prev_error = None
        self.prev_output = 0.0

    def compute(self, error, dt):
        """Advance one control step for the given error and return the output.

        Parameters
        ----------
        error : float
            Control error e(t) = reference - measurement.
        dt : float
            Time step in seconds (must be > 0 for derivative/integral terms).

        Returns
        -------
        float
            Saturated control output.
        """
        if dt <= 0:
            # No time elapsed: proportional term only, state untouched.
            return self._saturate(self.kp * error)

        derivative = 0.0
        if self.prev_error is not None:
            derivative = (error - self.prev_error) / dt

        i_prev = self.integral
        self.integral = i_prev + error * dt
        raw = (self.kp * error
               + self.ki * self.integral
               + self.kd * derivative)

        out = self._saturate(raw)
        if out != raw:
            # Anti-windup: undo integral growth that pushed us into saturation.
            if (raw > out and error > 0) or (raw < out and error < 0):
                self.integral = i_prev
            raw = self.kp * error + self.ki * self.integral + self.kd * derivative
            out = self._saturate(raw)

        self.prev_error = error
        self.prev_output = out
        return out

    def _saturate(self, value):
        if self.output_limits is None:
            return value
        low, high = self.output_limits
        return max(low, min(high, value))
