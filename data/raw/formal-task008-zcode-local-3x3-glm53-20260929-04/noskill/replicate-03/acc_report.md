# Adaptive Cruise Control (ACC) Simulation Report

## 1. System Design

### Architecture

The ACC system is a hierarchical controller built around two PID loops:

- **Speed loop (inner)**: a PID controller regulates the ego speed to a
  reference speed. Its output is the longitudinal acceleration command,
  saturated to the vehicle limits [-8.0, 3.0] m/s^2.
- **Distance loop (outer)**: a second PID converts the spacing error into
  a speed offset around the (EMA-filtered) lead vehicle speed. The
  reference speed handed to the inner loop is `min(set_speed, lead_speed +
  offset)`, so the ego never exceeds the set speed while following.

The ego state is integrated with simple kinematics at dt = 0.1 s
(speed += acceleration * dt), and the gap to the lead vehicle evolves as
gap += (lead_speed - ego_speed) * dt, anchored at the recorded distance
when a lead vehicle first appears in sensor_data.csv.

### Operating modes

| Mode | Trigger | Behaviour |
|------|---------|-----------|
| `cruise` | No lead vehicle detected (`lead_speed is None`) | Speed PID regulates ego speed to the set speed (30 m/s). |
| `follow` | Lead vehicle detected, TTC above the emergency threshold | Distance PID adjusts the reference speed around the lead speed to hold the desired gap. |
| `emergency` | TTC = distance / closing speed < 3 s | Commands the braking deceleration required to cancel the closing speed inside the minimum gap (capped at 8 m/s^2). |

### Safety features

- Time-headway policy: desired gap = 10 m + 1.5 s x ego speed (safety-critical spacing).
- TTC-based emergency braking below 3 s, using the physical stopping
  deceleration `closing_speed^2 / (2 * (gap - min_gap))`.
- Acceleration limits enforced on every command; deceleration limited to
  8 m/s^2, acceleration to 3.0 m/s^2.
- Anti-windup (conditional integration) on both PIDs so the integral term
  does not wind up while the command is saturated.
- The noisy lead-speed measurement is low-pass filtered (EMA, alpha =
  0.15, tau ~ 0.6 s) for the follow-mode control law; the TTC safety
  logic uses the raw measurement for the fastest possible reaction.
- The reference speed in follow mode is capped by the set speed, so the
  controller can never accelerate past the cruise speed.

## 2. PID Tuning Methodology and Final Gains

### Methodology

1. **Speed loop tuning (cruise scenario, no lead vehicle)**: a grid search
   over (kp, ki, kd) minimizing a cost combining rise time to 90% of the
   set speed, overshoot, steady-state speed error and control smoothness,
   subject to the acceleration limits. Anti-windup keeps the integral term
   inactive during the saturated launch ramp, allowing a high kp without
   overshoot.
2. **Distance loop tuning (full scenario with lead vehicle)**: with the
   speed gains fixed, a grid search over the distance-loop gains minimizing
   steady-state spacing error in the main following window (t = 45-70 s),
   subject to a hard minimum-gap constraint (> 5 m) and smoothness of the
   acceleration profile.
3. The tuned gains are stored in `tuning_results.yaml` and loaded at
   runtime by `simulation.py` (no tuning logic is embedded in the
   simulation itself).

### Final gains

| Loop | kp | ki | kd |
|------|----|----|----|
| Speed (pid_speed) | 5.0 | 0.05 | 0.0 |
| Distance (pid_distance) | 1.0 | 0.2 | 0.0 |

In follow mode the distance PID output acts as a speed offset (m/s) around
the lead speed, saturated to +/-10 m/s; the speed PID output is the
acceleration command (m/s^2).

## 3. Simulation Results and Performance Metrics

Scenario: 150 s of lead-vehicle data from sensor_data.csv (lead appears at
t = 30 s, hard-braking cut-in event at t = 120 s, lead disappears at
t = 130 s). Control duration 150 s at dt = 0.1 s (1501 samples).

| Metric | Target | Achieved | Result |
|--------|--------|----------|--------|
| Speed rise time (to 90% of set speed) | < 10 s | 9.00 s | PASS |
| Speed overshoot | < 5% | 0.02% | PASS |
| Speed steady-state error (cruise, t>=140 s) | < 0.5 m/s | 0.01 m/s | PASS |
| Distance steady-state error (t = 45-70 s) | < 2 m | 0.08 m | PASS |
| Minimum following distance | > 5 m | 13.46 m | PASS |
| Control duration | 150 s | 150.0 s | PASS |

Mode distribution over the 1501 control steps: `cruise` x 501, `emergency` x 20, `follow` x 980.

Additional observations:

- Peak speed 30.01 m/s (set speed 30 m/s) with no overshoot beyond the 5% envelope.
- Mean |jerk| 2.56 m/s^3, indicating a smooth
  acceleration profile across mode transitions.
- During the hard-braking cut-in at t = 120 s the controller brakes early
  and the gap never falls below 13.46 m (target > 5 m), unlike the
  recorded sensor trace whose gap dips to ~2 m.

Outputs: `simulation_results.csv` (1501 rows, columns time, ego_speed,
acceleration_cmd, mode, distance_error, distance, ttc) and this report.
