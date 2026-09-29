"""Discrete PID controller for the Adaptive Cruise Control simulation.

Implements the standard discrete PID law:

    u[k] = kp * e[k] + ki * sum_j(e[j] * dt) + kd * (e[k] - e[k-1]) / dt

The derivative acts on the error sequence (first-difference form), so the
very first call after reset() produces no derivative kick.

Anti-windup: if ``output_limits`` is given (as a (low, high) tuple), the
integral term is frozen whenever the raw output would exceed the limits
(conditional integration). With ``output_limits=None`` (the default) the
controller is the exact textbook PID with unbounded output.
"""


class PIDController:
    def __init__(self, kp, ki, kd, output_limits=None, d_filter_alpha=1.0):
        """
        Args:
            kp: proportional gain.
            ki: integral gain.
            kd: derivative gain.
            output_limits: optional (low, high) tuple. When given, outputs are
                clamped to [low, high] and the integral is frozen while the
                output is saturated (conditional-integration anti-windup).
            d_filter_alpha: exponential smoothing factor for the derivative
                term, 1.0 (default) = unfiltered exact first-difference PID.
        """
        self.kp = float(kp)
        self.ki = float(ki)
        self.kd = float(kd)
        self.d_filter_alpha = float(d_filter_alpha)
        if output_limits is not None:
            self.output_limits = (float(output_limits[0]), float(output_limits[1]))
        else:
            self.output_limits = None
        self.reset()

    def reset(self):
        """Clear the controller state (integral, previous error)."""
        self._integral = 0.0
        self._prev_error = None
        self._prev_derivative = 0.0
        self._saturated = False

    @property
    def integral(self):
        return self._integral

    def compute(self, error, dt):
        """Advance one control step.

        Args:
            error: current control error (setpoint - measurement).
            dt: time step in seconds (must be > 0).

        Returns:
            Control output as a float.
        """
        if dt is None or dt <= 0:
            raise ValueError("dt must be a positive time step in seconds")

        error = float(error)

        # Conditional-integration anti-windup: skip the integration while the
        # previous output was saturated, so the integral cannot run away.
        if not (self._saturated and self.output_limits is not None):
            self._integral += error * dt

        derivative = 0.0
        if self._prev_error is not None:
            raw_derivative = (error - self._prev_error) / dt
            derivative = (self.d_filter_alpha * raw_derivative
                          + (1.0 - self.d_filter_alpha) * self._prev_derivative)
            self._prev_derivative = derivative
        self._prev_error = error

        output = self.kp * error + self.ki * self._integral + self.kd * derivative

        if self.output_limits is not None:
            low, high = self.output_limits
            if output < low:
                self._saturated = True
                return low
            if output > high:
                self._saturated = True
                return high
            self._saturated = False
        return output
