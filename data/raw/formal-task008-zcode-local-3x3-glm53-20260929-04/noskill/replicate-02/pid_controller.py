"""PID controller for the Adaptive Cruise Control simulation.

Implements a textbook discrete PID controller:

    u(t) = kp * e(t) + ki * integral(e, 0..t) + kd * de/dt

State (integral accumulator and previous error) is kept internally and can
be cleared at any time with reset().
"""


class PIDController:
    """Discrete PID controller.

    Constructor: __init__(self, kp, ki, kd)
    Methods:
        reset()                  -> None  (clears integral and derivative state)
        compute(error, dt)       -> float (control output for this timestep)
    """

    def __init__(self, kp, ki, kd):
        self.kp = float(kp)
        self.ki = float(ki)
        self.kd = float(kd)
        self.integral = 0.0
        self.previous_error = 0.0

    def reset(self):
        """Clear all controller state (integral term and previous error)."""
        self.integral = 0.0
        self.previous_error = 0.0

    def compute(self, error, dt):
        """Compute the PID output for the given error at timestep dt.

        error : float — control error (setpoint - measurement)
        dt    : float — timestep in seconds (must be > 0)

        Returns the control output as a float.
        """
        error = float(error)
        if dt is None or dt <= 0.0:
            # No time advancement: proportional term only.
            return self.kp * error

        self.integral += error * dt
        derivative = (error - self.previous_error) / dt
        self.previous_error = error

        return self.kp * error + self.ki * self.integral + self.kd * derivative
