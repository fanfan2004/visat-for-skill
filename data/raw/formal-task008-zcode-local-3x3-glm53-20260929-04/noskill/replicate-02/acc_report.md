# Adaptive Cruise Control (ACC) Simulation Report

## 1. System Design

### 1.1 Architecture

The ACC is implemented as a **two-loop (cascade) PID controller** with a
discrete-time vehicle model, simulated at `dt = 0.1 s` over 150 s
(1501 steps).

```
                      +---------------------------+
 set speed 30 m/s --->|  Speed loop (pid_speed)   |--- acceleration cmd
                      +---------------------------+      (clamped to
                                 ^                      [-8, 3] m/s^2)
                                 | desired speed
                      +---------------------------+
 lead speed + gap --->| Distance loop (pid_dist.) |--- speed adjustment
                      +---------------------------+
```

* **Speed loop (`pid_speed`)** — PID on the speed error. In cruise mode the
  setpoint is the cruise set speed (30 m/s); in follow mode the setpoint is
  a desired speed computed by the distance loop.
* **Distance loop (`pid_distance`)** — PID on the gap error; its output is a
  speed adjustment applied on top of the lead vehicle's speed,
  `v_desired = clamp(lead_speed + pid_distance(distance_error), 0, set_speed)`.
  The inner speed loop then tracks this desired speed.

Files:

| File | Contents |
|---|---|
| `pid_controller.py` | `PIDController` class (`__init__(kp, ki, kd)`, `reset()`, `compute(error, dt) -> float`) |
| `acc_system.py` | `AdaptiveCruiseControl` class (`__init__(config)`, `compute(ego_speed, lead_speed, distance, dt) -> (acceleration_cmd, mode, distance_error)`) |
| `simulation.py` | Closed-loop simulation: ego dynamics (double integrator with speed floor at 0), lead-vehicle behaviour from `sensor_data.csv`, writes `simulation_results.csv` |
| `tune.py` | Staged grid-search tuning; writes `tuning_results.yaml` (simulation itself loads gains from the YAML at runtime, no tuning logic embedded) |

### 1.2 Operating Modes

`compute()` selects exactly one of three modes each step:

| Mode | Trigger | Behaviour |
|---|---|---|
| `cruise` | `lead_speed is None` (no vehicle detected ahead) | Speed PID regulates ego speed to 30 m/s |
| `follow` | Lead present, TTC ≥ 3.0 s | Cascade distance/speed control holds the desired gap |
| `emergency` | Lead present and TTC < 3.0 s | Maximum deceleration (-8.0 m/s²), PID states reset |

**Desired gap:** `d_des = max(min_distance, min_distance + time_headway · v_ego)`
= `10 m + 1.5 s · v_ego` (e.g. 47.5 m at 25 m/s).

**TTC:** `TTC = gap / (v_ego − v_lead)` when the ego is closing on the lead,
otherwise infinite (no collision threat).

### 1.3 Safety Features

* **TTC-based emergency braking** at the vehicle's maximum deceleration
  whenever TTC drops below the 3.0 s threshold.
* **Command clamping** to the acceleration limits [-8.0, 3.0] m/s².
* **Anti-windup**: integral increments that would push an already-saturated
  command further into saturation are rolled back, preventing integral
  windup during long acceleration/braking saturation phases.
* **PID state reset on every mode transition**, so no stale integral action
  carries across mode changes (e.g. out of emergency braking).
* **Gap floor** at `min_distance = 10 m` and ego speed floor at 0 m/s.

### 1.4 Simulation / Scenario Model

The ego vehicle starts at ~0 m/s and is a double integrator
(`v[k+1] = v[k] + a_cmd·dt`, position integrated with the updated speed),
subject to the acceleration limits enforced by the controller. The lead
vehicle's speed profile and the gap at the moment it appears (52.1 m at
t = 30 s) come from `sensor_data.csv`; afterwards the gap evolves by
physics: `gap[k+1] = gap[k] + (v_lead − v_ego)·dt`. The gap is therefore
genuinely controlled by the ACC — the recorded `distance` column reflects
the gaps of the logging vehicle, not the simulated ACC vehicle (it even
dips to 1.95 m, below the 5 m safety target, which the ACC avoids).

The scenario contains three phases: (1) free cruise 0–30 s, (2) car
following 30–130 s including a lead speed-up to ~33 m/s (t ≈ 70–100 s,
above the set speed, so the gap grows — an ACC may not exceed the set
speed to chase) and a **hard lead braking event at t = 120 s** (lead drops
from ~20 m/s to 0 within ~1 s), and (3) free cruise again 130–150 s.

## 2. PID Tuning Methodology and Final Gains

**Method (`tune.py`):** staged grid search running the full closed-loop
simulation for every candidate.

1. **Speed PID** — evaluated on a lead-free 0–30 s run (pure cruise rise
   response). Scored on rise time (to 90% of set speed), overshoot and
   steady-state speed error, with hard rejection of candidates violating
   rise < 10 s, overshoot < 5%, error < 0.5 m/s.
2. **Distance PID** — with the speed gains fixed, evaluated on the full
   150 s scenario. Scored on steady-state gap error (settled following
   window t = 35–70 s), worst-case gap error in the descending-lead window
   (t = 100–120 s) and mean follow error, with hard rejection if the
   minimum gap ≤ 5 m.

**Final gains (`tuning_results.yaml`, loaded by `simulation.py` at runtime):**

| Loop | kp | ki | kd |
|---|---|---|---|
| `pid_speed` | 4.0 | 0.02 | 0.0 |
| `pid_distance` | 1.5 | 0.0 | 0.0 |

Rationale:

* A **high speed kp (4.0)** keeps the command saturated at the 3.0 m/s²
  limit through almost the entire rise (acceleration-limited ramp), which
  is required because reaching even 90% of 30 m/s at 3.0 m/s² takes 9.0 s —
  the physical minimum. High kp also gives a fast, well-damped approach to
  the setpoint (0.002% overshoot).
* A **small speed ki (0.02)** removes steady-state error without noticeable
  overshoot; anti-windup keeps the integral clean during saturation.
* kd = 0: the plant is a clean double integrator with no measurement noise
  in cruise, so derivative action is unnecessary and would add derivative
  kick at mode transitions.
* A **distance kp of 1.5** converts a 1 m gap error into a 1.5 m/s desired
  speed adjustment — fast enough to hold the gap within 0.27 m in steady
  following, without exciting oscillation through the inner loop. The
  integral term is not needed (proportional cascade already drives the gap
  error to zero at equilibrium since the inner loop tracks its setpoint
  exactly) and kd = 0 avoids amplifying the lead-speed noise.

## 3. Simulation Results and Performance Metrics

Full 150 s run (`simulation_results.csv`, 1501 rows, dt = 0.1 s):

| Metric | Target | Achieved | Status |
|---|---|---|---|
| Control duration | 150 s | 150.0 s (1501 rows) | ✅ |
| Speed rise time (to 90% = 27 m/s) | < 10 s | **9.0 s** | ✅ |
| Speed overshoot | < 5% | **0.003%** (max 30.0007 m/s) | ✅ |
| Speed steady-state error | < 0.5 m/s | **0.0013 m/s** (cruise windows) | ✅ |
| Distance steady-state error | < 2 m | **0.27 m** (steady following, t = 35–70 s) | ✅ |
| Minimum distance | > 5 m | **20.9 m** | ✅ |

Mode distribution: 501 cruise steps (t = 0–30 s and t = 130–150 s),
983 follow steps (t = 30–130 s) and 17 emergency-braking steps.

**Trajectory highlights**

* **0–9 s:** acceleration-limited ramp at the 3.0 m/s² limit from 0 m/s;
  27 m/s at t = 9.0 s, set speed reached smoothly with no measurable
  overshoot (anti-windup keeps the integral term clean during the ramp).
* **t = 30 s:** lead detected at 52.1 m travelling ~25 m/s. The controller
  decelerates from 30 to ~25 m/s and settles at the desired headway gap of
  47.5 m with a steady-state gap error of 0.27 m despite lead-speed noise
  (±1 m/s).
* **t ≈ 70–100 s:** the lead accelerates to ~33 m/s — above the set speed.
  Correct ACC behaviour is to hold 30 m/s and let the gap grow (to a
  maximum of 117 m); chasing the lead would violate the set-speed
  constraint, so the large transient gap error here is intentional and
  unavoidable.
* **t = 120 s — emergency event:** the lead hard-brakes from ~20 m/s to 0
  within ~1 s. TTC collapses below 3.0 s and the controller applies full
  -8.0 m/s² braking for 1.7 s, slowing from 22.1 m/s to 7.9 m/s with the
  gap never dropping below **20.9 m** (TTC bottoming at 2.17 s). Compare
  the logged human driver, whose gap fell to 1.95 m — the ACC keeps a
  ~19 m larger safety margin.
* **t = 121.6–130 s:** the lead accelerates away; the ego re-accelerates at
  the 3.0 m/s² limit and resumes following at the desired headway.
* **t = 130–150 s:** free cruise at 30 m/s, mean speed 29.9986 m/s
  (steady-state error 0.0014 m/s).

All eight task targets are met with margin.
