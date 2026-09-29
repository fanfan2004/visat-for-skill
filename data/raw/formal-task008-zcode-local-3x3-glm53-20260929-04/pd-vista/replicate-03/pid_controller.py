"""Generic discrete PID controller used by the adaptive cruise control system.

Error convention: a positive error produces a positive (non-negative) output
contribution, so the caller decides the physical meaning of the error sign.
"""


class PIDController:
    """Discrete PID controller with optional anti-windup.

    Parameters
    ----------
    kp, ki, kd : float
        Proportional, integral and derivative gains.
    integral_limit : float, optional
        Maximum absolute value of the integral *contribution* (``ki * integral``)
        to the output. ``None`` (default) disables the clamp. This prevents
        integrator windup while the output is saturated.
    """

    def __init__(self, kp, ki, kd, integral_limit=None, output_limits=None):
        self.kp = float(kp)
        self.ki = float(ki)
        self.kd = float(kd)
        self.integral_limit = None if integral_limit is None else float(integral_limit)
        if output_limits is not None:
            self.output_limits = (float(output_limits[0]), float(output_limits[1]))
        else:
            self.output_limits = None
        self.reset()

    def reset(self):
        """Clear the integrator and derivative memory."""
        self.integral = 0.0
        self._prev_error = None
        self.last_output = 0.0

    def compute(self, error, dt):
        """Advance one control step and return the raw (unclamped) PID output.

        Parameters
        ----------
        error : float
            Control error at the current step.
        dt : float
            Time step in seconds. A non-positive ``dt`` skips the integral and
            derivative updates and returns the proportional term only.

        Returns
        -------
        float
            ``kp*error + ki*integral + kd*derivative``.
        """
        error = float(error)
        p_term = self.kp * error

        d_term = 0.0
        if dt > 0.0 and self._prev_error is not None:
            d_term = self.kd * (error - self._prev_error) / dt

        candidate_integral = self.integral + error * dt if dt > 0.0 else self.integral

        def _limit_integral(value):
            if self.integral_limit is not None and self.ki != 0.0:
                max_integral = self.integral_limit / abs(self.ki)
                return max(-max_integral, min(max_integral, value))
            return value

        if self.output_limits is not None:
            lo, hi = self.output_limits
            # Conditional integration: reject this step's integral accumulation
            # when the resulting output would leave the limits (anti-windup).
            out_unsat = p_term + self.ki * _limit_integral(candidate_integral) + d_term
            if out_unsat > hi or out_unsat < lo:
                i_term = self.ki * self.integral
                output = max(lo, min(hi, p_term + i_term + d_term))
            else:
                self.integral = _limit_integral(candidate_integral)
                output = out_unsat
        else:
            self.integral = _limit_integral(candidate_integral)
            output = p_term + self.ki * self.integral + d_term

        self._prev_error = error
        self.last_output = output
        return output
