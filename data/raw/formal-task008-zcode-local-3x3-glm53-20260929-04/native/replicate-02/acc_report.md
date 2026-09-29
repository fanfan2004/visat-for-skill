# Adaptive Cruise Control — Simulation Report

## 1. System Design

### 1.1 Architecture

The ACC system is a layered feedback controller (`acc_system.py`) driving a
kinematic vehicle plant in a discrete-time simulator (`simulation.py`,
dt = 0.1 s, 150 s horizon):

- **Speed control layer** — a PID controller (`pid_controller.py`) regulates
  ego speed. In cruise mode the error is `set_speed − ego_speed`; in follow
  mode the error is `desired_speed − ego_speed`, where `desired_speed` is
  produced by the distance layer.
- **Distance control layer** — a second PID acts on the gap error
  `distance − (time_headway · ego_speed + min_distance)` and outputs a speed
  increment above the lead vehicle's speed. This cascaded structure (distance
  PID → target speed → speed PID → acceleration) keeps the ego at the lead's
  speed when the gap is correct, closes or opens the gap when it is not, and
  never exceeds the cruise set speed.
- **Safety layer** — time-to-collision (TTC) monitoring with emergency
  braking, acceleration clamping to the vehicle limits [−8.0, 3.0] m/s², and
  anti-windup on both PIDs.

### 1.2 Operating modes

| Mode | Trigger | Behavior |
|---|---|---|
| `cruise` | no lead vehicle detected (`lead_speed is None`) | speed PID regulates ego to the 30 m/s set speed |
| `follow` | lead present, TTC ≥ 3.0 s | target speed = lead speed + gap-correction increment from the distance PID, capped at the set speed |
| `emergency` | lead present, TTC < 3.0 s | controlled constant-deceleration braking `a = −v² / (2·(d − stop_gap))`, clamped to the −8.0 m/s² vehicle limit; PIDs frozen to prevent windup |

### 1.3 Safety features

- **Emergency braking profile**: rather than a fixed full-brake command, the
  emergency law computes the constant deceleration that brings the ego to a
  stop exactly at the minimum gap plus a 2 m margin, so braking is as gentle
  as safety allows and the ego ends the event at a usable following distance.
- **Bounded gap closing**: in follow mode the gap-closing increment is
  clamped to `min(5 m/s, distance/4)`, which guarantees the closing TTC never
  falls below 4 s through controller action — the ego can never chase itself
  into an emergency situation while closing a large gap.
- **Anti-windup**: conditional integration on both PIDs (integral frozen while
  the output is saturated in the direction of the error) plus integral
  magnitude limits. This eliminated the large speed overshoot observed during
  tuning with naive integration.
- **Acceleration clamping** to the physical limits [−8.0, 3.0] m/s² and
  non-negative speed.

### 1.4 Simulation model

- Lead vehicle: `lead_speed` profile replayed from `sensor_data.csv`; the
  recorded `distance` (52.10 m at t = 30 s) seeds the initial gap, after
  which the gap evolves kinematically: `d(t+dt) = d(t) − (ego − lead)·dt`.
- Ego: `v(t+dt) = clamp(v(t) + a(t)·dt, 0, ∞)`, position integrated with the
  trapezoidal rule.
- The recorded data contains a cut-in event at t ≈ 120 s (the lead drops from
  ~20 m/s to ~5 m/s and stops). The recorded driver braked at ~5 m/s² and came
  within 1.95 m; our ACC anticipates the closing gap, enters emergency braking
  at t = 120.2 s, and keeps ≥ 22.3 m of gap throughout.

## 2. PID Tuning Methodology and Final Gains

### 2.1 Method

Gains were tuned offline (`tune.py`) by grid-searching both controllers and
scoring each candidate by running the full 150 s closed-loop simulation
(3,024 candidates evaluated). The score sums hard-constraint violations
(rise time < 10 s, overshoot < 5 %, speed steady-state error < 0.5 m/s,
distance steady-state error < 2 m, minimum distance > 5 m, gains within
their allowed ranges) weighted heavily, plus soft terms: settled tracking
error and a small penalty on harsh acceleration. The search followed the
classic manual procedure implicitly: Kp sets response speed, Ki removes
steady-state error, Kd damps overshoot.

### 2.2 Final gains (`tuning_results.yaml`)

| Controller | Kp | Ki | Kd |
|---|---|---|---|
| `pid_speed` | 1.2 | 0.0 | 0.1 |
| `pid_distance` | 0.1 | 0.05 | 0.0 |

- **Speed loop**: Kp = 1.2 gives a fast, accel-limited rise (the plant is a
  pure integrator, so proportional action alone already yields zero
  steady-state error); Ki = 0 was selected because the integral term added
  overshoot without benefit in this disturbance-free model (anti-windup is
  implemented and active if a Ki > 0 config is used); Kd = 0.1 adds light
  damping at the end of the rise.
- **Distance loop**: Kp = 0.1 and Ki = 0.05 drive the gap error to zero in
  sustained following (the integral term also removes the residual error
  caused by the noisy lead-speed profile); Kd = 0 avoids amplifying the
  radar noise present in the lead-speed data.

## 3. Simulation Results and Performance Metrics

150 s simulation, 1,501 steps (dt = 0.1 s), set speed 30 m/s, initial speed
0 m/s (`simulation_results.csv`).

| Metric | Target | Achieved | Status |
|---|---|---|---|
| Speed rise time (10 % → 90 % of 30 m/s) | < 10 s | **8.0 s** | ✅ |
| Speed overshoot | < 5 % | **0.0 %** (max ego speed 30.0 m/s) | ✅ |
| Speed steady-state error | < 0.5 m/s | **< 0.001 m/s** (cruise windows t = 15–30 s and t = 135–150 s) | ✅ |
| Distance steady-state error | < 2 m | **0.30 m** mean abs. over settled follow (t = 35–72 s); **1.01 m** signed mean over the final 10 % of follow rows | ✅ |
| Minimum following distance | > 5 m | **22.32 m** | ✅ |
| Control duration | 150 s | 150.0 s, 1,501 rows | ✅ |
| Acceleration limits | [−8.0, 3.0] m/s² | commands within [−8.0, 3.0] | ✅ |

Mode usage: `cruise` 501 rows (t = 0–29.9 s and t = 130–150 s), `follow`
983 rows, `emergency` 17 rows (t = 120.2–121.8 s, triggered by the cut-in
event in the sensor data).

### 3.1 Trajectory summary

- **t = 0–10 s**: full-throttle accel-limited rise from 0 to 30 m/s; the
  speed PID saturates at 3.0 m/s² then converges smoothly to the set speed
  with no overshoot.
- **t = 30–130 s**: lead vehicle present. The ego decelerates from 30 m/s,
  settles into time-headway following (gap ≈ 1.5·v + 10 m) with a mean gap
  error of 0.30 m during sustained tracking, holds 30 m/s while the lead
  runs faster (t = 72–100 s), then tracks the lead's deceleration.
- **t = 120.2–121.8 s**: the cut-in event drives TTC to a minimum of 2.15 s;
  emergency braking engages (peak decel −8.0 m/s²) and the gap never falls
  below 22.32 m — versus 1.95 m in the recorded real-world driving data.
- **t = 122–130 s**: smooth recovery to headway following as the lead
  accelerates away.
- **t = 130–150 s**: lead disappears; cruise mode returns the ego to
  30 m/s with zero steady-state error.

### 3.2 Notes

- The distance error grows transiently while the lead exceeds the set speed
  (t = 72–110 s): a correct ACC must not exceed the cruise set speed, so the
  gap widens legitimately; the controller re-closes it once the lead slows.
  Steady-state metrics are therefore reported over settled tracking windows.
- All PID gains respect the required ranges: kp ∈ (0, 10), ki ∈ [0, 5),
  kd ∈ [0, 5).
