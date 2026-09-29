# Adaptive Cruise Control (ACC) — Design & Simulation Report

## 1. System design

### Architecture

The ACC is a supervisory longitudinal controller (`AdaptiveCruiseControl` in
`acc_system.py`) built around two decoupled PID loops (`PIDController` in
`pid_controller.py`):

* **Speed loop (`pid_speed`)** — active in cruise mode; regulates ego speed to
  the driver-set speed (30 m/s).
* **Distance loop (`pid_distance`)** — active in follow mode; regulates the gap
  to the lead vehicle.

A simple longitudinal plant (`EgoVehicle`) integrates the (clamped)
acceleration command into speed and speed into position. The lead-vehicle
scenario (speed profile and initial gap) comes from `sensor_data.csv`; the lead
position is anchored at the first detection using the measured distance and
then integrated with the lead speed, so the loop is fully closed and reacts to
ego braking/acceleration.

### Modes

Selected every timestep by `compute(ego_speed, lead_speed, distance, dt)`,
which returns `(acceleration_cmd, mode, distance_error)`:

| Mode | Trigger | Control law |
|---|---|---|
| `cruise` | `lead_speed is None` | speed PID on `e = set_speed − ego_speed` |
| `follow` | lead present, TTC ≥ 3.0 s | distance PID on `e = desired_distance − distance` (positive error ⇒ decelerate) |
| `emergency` | lead present and TTC = distance / closing_speed < 3.0 s | full braking at `max_deceleration` (−8.0 m/s²) |

`distance_error` is the spacing error `desired − actual` (positive when the ego
is too close); it is `None` in cruise mode. TTC is `None` (reported empty in
the CSV) when the ego is not approaching the lead.

### Spacing policy

The desired gap follows the time-headway model with a standstill gap:

```
desired_distance = time_headway · ego_speed + min_distance
                 = 1.5 · v_ego + 10.0 m
```

This never requests less than the 10 m minimum gap and keeps a speed-
proportional gap above it.

### Safety features

* Acceleration commands clamped to the actuator range `[−8.0, 3.0] m/s²`
  (both in the controller and in the plant).
* PID output limits + conditional-integration anti-windup: the integrator is
  frozen while saturated, preventing wind-up during the long full-throttle
  ramp and during emergency braking (this is what keeps overshoot at 0.01 %).
* Speed cap: in follow mode the ACC never commands speed above the driver-set
  speed (matches the recorded ego, which never exceeds 30 m/s even when the
  lead pulls away at up to 36.8 m/s).
* The spacing policy never requests a gap below `min_distance` (10 m).
* Ego speed floored at 0 m/s (no reversing).
* Emergency braking as a hard backstop independent of the PID loops.
* PID states are reset on cruise re-entry so stale integrator/derivative state
  never leaks across scenario phases; the distance PID keeps updating during
  emergency (without its output being used) so re-entry into follow is smooth.

## 2. PID tuning methodology and final gains

Tuning was performed offline by grid search (`tune_pid.py`) over the full
150 s closed-loop scenario, evaluated on simulated metrics against the task
targets. The tuner runs in two stages (speed loop first with the distance loop
at a sane starting point, then the distance loop with the tuned speed gains)
and scores each candidate with a weighted cost (rise time, overshoot,
steady-state errors, min-gap margin, mode chatter, control smoothness);
candidates violating a hard target (rise ≥ 10 s, overshoot ≥ 5 %, speed error
≥ 0.5 m/s, distance error ≥ 2 m, minimum gap < 10 m) are rejected outright.

Searched ranges respected the required bounds kp ∈ (0, 10), ki ∈ [0, 5),
kd ∈ [0, 5). `simulation.py` embeds no tuning logic — the final gains are
loaded from `tuning_results.yaml` at runtime.

Final gains:

| Loop | kp | ki | kd |
|---|---|---|---|
| `pid_speed` | 5.0 | 0.2 | 0.0 |
| `pid_distance` | 2.0 | 0.2 | 0.2 |

Rationale: the speed loop uses a high proportional gain so the acceleration
command stays saturated at +3.0 m/s² for almost the whole ramp (minimum-time
behaviour — 30 m/s from standstill needs at least 10 s at 3 m/s²), with a small
integral term to close the residual error quickly once desaturated; derivative
action is unnecessary because the loop has no measurement noise. The distance
loop needs a stronger proportional gain than a bare minimum-time design
because it must brake ahead of the lead's decelerations while holding the
larger 1.5·v + 10 m gap; the derivative term damps the second-order gap
dynamics and the integral term removes any residual spacing offset.

## 3. Simulation results and performance metrics

150 s closed-loop simulation at dt = 0.1 s (`simulation.py` →
`simulation_results.csv`, 1501 rows). Scenario: standstill → 30 m/s cruise
(t = 0–30 s); lead vehicle present from t = 30 s to t = 129.9 s (slower
traffic at ≈25 m/s, an acceleration phase, a hard lead brake from ≈20 m/s to a
stop at t ≈ 120 s with TTC ≈ 1.9 s triggering emergency braking, then
recovery); lead lost at t = 130 s → cruise resumed.

| Metric | Target | Achieved |
|---|---|---|
| Speed rise time (to 90 % = 27 m/s) | < 10 s | **9.0 s** |
| Time to full setpoint (30 m/s) | — | 10.6 s (physical minimum is 10.0 s at 3 m/s²) |
| Speed overshoot | < 5 % | **0.01 %** (peak 30.003 m/s) |
| Speed steady-state error (cruise) | < 0.5 m/s | **0.002 m/s** |
| Distance steady-state error (steady following, t = 42–65 s) | < 2 m | **0.08 m** |
| Minimum following distance | > 5 m (and ≥ min_distance = 10 m) | **18.67 m** |
| Control duration | 150 s | 150 s (1501 rows at dt = 0.1 s) |
| Follow↔emergency mode chatter | — | 2 transitions (one clean emergency event) |

Key observations:

* The speed loop saturates at +3.0 m/s² for the first ≈9.3 s (bang-bang-like
  minimum-time ramp), then the integral term settles the speed onto 30 m/s
  with no measurable overshoot thanks to anti-windup.
* During steady car-following (t ≈ 35–65 s) the controller holds the requested
  1.5 s-headway gap (≈47.5 m at 25 m/s) with ≈0.1 m error.
* The larger spacing policy makes the ego brake ahead of the lead's gradual
  decelerations; when the lead brakes abruptly (≈20 → 0 m/s at t = 120 s, TTC
  collapsing to 1.94 s), emergency braking engages within one control step at
  −8.0 m/s² and the gap bottoms out at 18.67 m — far above both the 5 m target
  and the 10 m minimum gap, and a large improvement over the 1.95 m minimum
  recorded in the reference sensor data.
* After the lead is lost (t = 130 s) cruise resumes and speed returns to
  30 m/s with zero steady-state error.

Artifacts: `pid_controller.py`, `acc_system.py`, `simulation.py`,
`tune_pid.py` (tuning tool), `tuning_results.yaml`, `simulation_results.csv`.
