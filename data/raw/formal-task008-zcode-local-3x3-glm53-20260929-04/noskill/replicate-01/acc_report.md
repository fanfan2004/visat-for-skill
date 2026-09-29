# Adaptive Cruise Control (ACC) — Design, Tuning and Simulation Report

**Date:** 2026-09-29
**Scope:** PID-based adaptive cruise control, 150 s closed-loop simulation at 0.1 s resolution (1501 steps).

---

## 1. System Design

### 1.1 Architecture

The system is split into three layers (files in this directory):

| Module | Responsibility |
|---|---|
| `pid_controller.py` | `PIDController` — generic discrete PID (P + I + D on the error sequence) with optional output clamping and conditional-integration anti-windup, plus an optional first-order derivative filter. |
| `acc_system.py` | `AdaptiveCruiseControl` — the ACC supervisor: mode selection (cruise / follow / emergency), desired-spacing law, guaranteed-stopping safety guard, and mapping of controller output to an acceleration command within the vehicle envelope. |
| `simulation.py` | Plant and scenario driver: ego point-mass kinematics (`v[k+1] = v[k] + a[k]·dt`, speed ≥ 0), gap propagation by relative speed, lead-vehicle data ingestion from `sensor_data.csv`, gain loading from `tuning_results.yaml` at runtime, and CSV output. |

The control loop per 0.1 s step: the sensor supplies the ego speed and the lead-vehicle state → `AdaptiveCruiseControl.compute(ego_speed, lead_speed, distance, dt)` returns `(acceleration_cmd, mode, distance_error)` → the command is saturated to the vehicle envelope `[-8.0, 3.0] m/s²` → the ego state is integrated → the gap is propagated as `gap[k+1] = gap[k] + (v_lead[k] − v_ego[k])·dt`, clamped at 0.

### 1.2 Operating modes

Mode is selected every step from the sensor data:

| Mode | Condition | Behaviour |
|---|---|---|
| `cruise` | `lead_speed is None` (no vehicle detected ahead) | Speed PID regulates ego speed to `set_speed` = 30 m/s. Error = `set_speed − ego_speed`. |
| `follow` | Lead present, TTC ≥ `emergency_ttc_threshold` (3.0 s) | Distance PID regulates the gap to the desired spacing. Error = `actual_distance − desired_distance` (positive = too far → accelerate; negative = too close → brake). |
| `emergency` | Lead present and TTC < 3.0 s (or gap ≤ 0) | Maximum deceleration (`−8.0 m/s²`) commanded without modulation — TTC below the threshold means the situation is already critical. |

The desired spacing follows a constant-time-headway law with a standstill-gap offset:

```
desired_distance = min_distance (10 m) + time_headway (1.5 s) × ego_speed
```

so the controller tracks the 1.5 s headway at speed and the commanded spacing never falls below the 10 m minimum gap (e.g. 47.5 m at 25 m/s, 55 m at the 30 m/s set speed).

### 1.3 Safety features

* **Acceleration envelope** — every command is clamped to `[-8.0, 3.0] m/s²`; ego speed is clamped to ≥ 0.
* **TTC-based emergency braking** — time-to-collision `TTC = gap / closing_speed` (infinite when not closing); below the 3.0 s threshold the controller immediately applies maximum braking, which reacts at the earliest physically possible instant of the recorded cut-in/hard-brake event at t = 120 s.
* **Guaranteed-stopping guard (follow mode)** — independent of the PID, the controller computes the deceleration needed to cancel the closing speed by the time the gap shrinks to `min_distance` (`a = −1.5 · Δv² / (2 · (gap − 10 m))`, clamped to the envelope) and applies it whenever the PID would brake less. This keeps the minimum-gap constraint satisfied even under sudden lead deceleration or sluggish PID response.
* **Set-speed ceiling in follow mode** — the ACC never accelerates past the driver's 30 m/s set speed to close a gap, so gap-keeping cannot create speed overshoot.
* **Standstill guard** — at zero speed no further deceleration is commanded, and launching from standstill only starts once the gap has re-opened.
* **PID anti-windup** — both PIDs use conditional integration (the integral is frozen while the output is saturated at the acceleration limits), which prevents integrator wind-up during the saturated start-up ramp and the emergency braking phase, keeping overshoot negligible and recovery clean.
* **Derivative filtering** — the distance PID applies a light first-order filter to its derivative term, since the gap derivative carries lead-speed sensor noise; the speed PID uses the exact unfiltered derivative (its error is noise-free).

---

## 2. PID Tuning Methodology and Final Gains

### 2.1 Method

A two-stage constrained grid search (temporary harness `tune.py`, removed after use) scored candidate gains on closed-loop simulations:

1. **Stage 1 — speed PID (`pid_speed`)**: tuned on the recorded scenario's start-up (0 → 30 m/s from standstill). Cost = hard penalties for missing rise/overshoot/steady-state targets plus soft terms for rise speed and control smoothness.
2. **Stage 2 — distance PID (`pid_distance`)**: tuned with the Stage-1 speed gains fixed, on four scenarios — the full recorded 150 s scenario, a constant-speed-lead approach from 60 m gap, a matched-speed spacing capture, and a decelerating-lead tracking case. Cost = steady-state gap error, hard penalty for any dip below the 10 m minimum-gap constraint, speed oscillation while following, and acceleration smoothness.

Gain bounds were respected as required: `kp ∈ (0, 10)`, `ki ∈ [0, 5)`, `kd ∈ [0, 5)`.

Key trade-offs resolved during tuning:

* Rise time is bounded below by physics: from 0 m/s at the 3.0 m/s² ceiling, the ego can reach 27 m/s (90 % of set speed) at t = 9.0 s and 30 m/s no earlier than t = 10.0 s. High `kp` keeps the loop saturated on the full-acceleration ramp; integral action (with anti-windup) is needed so the speed actually crosses the 30 m/s set point instead of approaching it asymptotically; `ki` must stay moderate so the saturated-ramp integrator does not produce overshoot.
* For the distance loop under the offset spacing law, proportional action dominates the tracking; the derivative term (effectively a relative-speed damper) damps the response to lead-speed changes, and the guaranteed-stopping guard handles the extreme deceleration event, so no integral action is needed (`ki = 0` keeps the loop free of wind-up during emergencies).

### 2.2 Final gains (`tuning_results.yaml`)

| Loop | kp | ki | kd |
|---|---|---|---|
| `pid_speed` | 7.0 | 0.5 | 0.0 |
| `pid_distance` | 2.0 | 0.0 | 0.2 |

---

## 3. Simulation Results and Performance Metrics

### 3.1 Setup

150 s at dt = 0.1 s (1501 steps), initial speed 0 m/s. Lead-vehicle data (`lead_speed`, and the gap reading at first detection) is taken from `sensor_data.csv`; the ego and the gap are simulated in closed loop. Full time series in `simulation_results.csv` (columns: `time, ego_speed, acceleration_cmd, mode, distance_error, distance, ttc`; `distance_error`, `distance` and `ttc` are empty while no lead is detected).

### 3.2 Performance vs. targets

| Metric | Target | Achieved | Status |
|---|---|---|---|
| Speed rise time | < 10 s | **9.0 s** (0→90 % of 30 m/s); reaches 30.0 m/s at 10.0 s | PASS |
| Speed overshoot | < 5 % | **1.00 %** (peak 30.30 m/s) | PASS |
| Speed steady-state error | < 0.5 m/s | **0.009 m/s** | PASS |
| Distance steady-state error | < 2 m | **0.32 m** | PASS |
| Minimum distance | > 5 m (and ≥ 10 m minimum-gap constraint) | **17.35 m** | PASS |
| Control duration | 150 s | **150.0 s** (1501 steps) | PASS |

Mode usage: cruise 501 steps, follow 982 steps, emergency 18 steps.

### 3.3 Scenario walkthrough

* **t = 0–10 s (cruise):** full 3.0 m/s² acceleration from standstill; 27 m/s at t = 9.0 s, 30 m/s at t = 10.0 s (the physical floor given the 3.0 m/s² limit), small 1.00 % overshoot absorbed by anti-windup.
* **t = 30 s:** lead vehicle detected 52.1 m ahead at ≈ 25 m/s. The ego holds 30 m/s until the gap reaches the 55 m desired headway, then brakes smoothly and captures the headway spacing.
* **t = 35–60 s (steady following):** ego settles at the lead speed (≈ 25 m/s) with the gap at the desired 47.5 m — max gap error 0.32 m, well inside the 2 m target.
* **t = 60–120 s:** the ACC tracks the lead through its acceleration (25 → 30 m/s), holds 30 m/s at the set-speed ceiling while the lead runs faster, then follows the lead's gradual deceleration back to ≈ 20 m/s while restoring the headway gap.
* **t = 120–122 s (emergency):** the lead cuts speed abruptly from ≈ 20 m/s to ≈ 3 m/s within one sample (no anticipation is physically possible). TTC drops below 3.0 s at t = 120.0 s and the controller immediately applies −8.0 m/s², then the guaranteed-stopping guard and the distance PID take over. Minimum gap of the whole run is **17.35 m** — comfortably above both the 5 m safety target and the 10 m minimum-gap constraint. As the lead recovers, the controller resumes following and re-establishes the headway.
* **t = 130–150 s (cruise):** lead leaves sensor range; the ego returns to 30 m/s and holds it with ≤ 0.009 m/s steady-state error.

### 3.4 Notes and conventions

* `distance_error = actual_distance − desired_distance`: positive means too far (accelerate), negative means too close (brake). In `cruise` mode no lead is tracked and the field is empty.
* `ttc = gap / closing_speed` and is reported only while actively closing on a detected lead (empty otherwise or when not closing).
* The `distance` column is the simulated closed-loop gap, initialised from the sensor reading when a lead is first detected and propagated with the relative speed afterwards; the `sensor_data.csv` `distance` column is the recorded gap of the source vehicle, used as the detection-time initialisation.

## 4. Files Delivered

| File | Content |
|---|---|
| `pid_controller.py` | `PIDController` class (`__init__(kp, ki, kd)`, `reset()`, `compute(error, dt)`) |
| `acc_system.py` | `AdaptiveCruiseControl(config)` with `compute(ego_speed, lead_speed, distance, dt)` → `(acceleration_cmd, mode, distance_error)` |
| `simulation.py` | Runtime gain loading, plant model, 150 s run, CSV output |
| `tuning_results.yaml` | Tuned `pid_speed` / `pid_distance` gains |
| `simulation_results.csv` | 1501-row simulation time series |
| `acc_report.md` | This report |
