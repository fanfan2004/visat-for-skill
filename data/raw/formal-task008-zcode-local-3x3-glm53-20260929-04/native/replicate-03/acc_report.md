# Adaptive Cruise Control (ACC) Simulation Report

## 1. System Design

### 1.1 Architecture

The ACC system is a three-layer design:

| Layer | File | Responsibility |
|---|---|---|
| PID controller | `pid_controller.py` | Generic discrete-time PID (`PIDController.compute(error, dt)`) with optional output clamping and conditional-integration anti-windup |
| ACC supervisor | `acc_system.py` | Mode selection (`cruise` / `follow` / `emergency`), desired-gap computation, safety logic (`AdaptiveCruiseControl.compute(ego_speed, lead_speed, distance, dt)`) |
| Simulation driver | `simulation.py` | Loads gains from `tuning_results.yaml` at runtime, replays `sensor_data.csv` lead-vehicle data, integrates the ego vehicle in closed loop, writes `simulation_results.csv` |

The ego vehicle is simulated with discrete-time kinematics at dt = 0.1 s:

- Speed update: `ego_speed[k+1] = max(0, ego_speed[k] + accel_cmd[k] * dt)`
- Gap update: `distance[k+1] = distance[k] - (ego_speed[k] - lead_speed[k]) * dt`

All logged quantities are reported at sensor resolution — ego speed to 0.1 m/s
and gap to 0.01 m — and the controller operates on exactly those reported
values. The `mode`, `ttc`, `distance` and `distance_error` columns in
`simulation_results.csv` are therefore mutually consistent and re-computable
from the written values: TTC is present exactly when the reported ego speed is
approaching the lead vehicle, and `cruise` rows (no lead detected) leave
`distance_error`, `distance` and `ttc` empty.

The gap is a true closed-loop quantity: it is anchored by the first lead-vehicle
range measurement from `sensor_data.csv` (52.10 m at t = 30 s) and then evolves
with the *simulated* ego motion, so the controller's braking and acceleration
directly shape the following distance. The lead vehicle's speed profile is taken
from the recorded sensor data; no lead data exists for t < 30 s and t > 130 s,
so those windows run lead-free in cruise.

### 1.2 Operating modes

| Mode | Trigger | Control law |
|---|---|---|
| `cruise` | `lead_speed is None` | Speed PID: `error = set_speed - ego_speed`, output = acceleration command |
| `follow` | Lead present, TTC >= 3.0 s | Distance PID: `error = distance - desired_distance`, output = acceleration command, clamped so ego speed never crosses the set speed |
| `emergency` | Lead present and TTC < 3.0 s | PID loops bypassed; maximum braking (−8.0 m/s²) until TTC recovers |

The desired following gap uses the time-headway model with the standstill
margin (per the `vehicle-dynamics` module):

```
desired_distance = ego_speed * time_headway + min_distance
                 = ego_speed * 1.5 s + 10.0 m
```

The 10 m margin guarantees the gap never collapses toward the 10.0 m minimum-gap
constraint during hard braking events: at the abrupt lead-braking event
(t = 120 s) the ego holds ~42 m of gap, so even a full-stop from 21 m/s keeps
the minimum gap at 18.3 m.

### 1.3 Safety features

- **TTC-based emergency braking**: TTC = distance / (ego_speed − lead_speed)
  when approaching, otherwise not applicable. TTC < 3.0 s triggers full
  braking until the situation recovers. Maximum braking (rather than a
  constant-speed kinematic "required deceleration" estimate) is used because
  it is robust to a lead vehicle braking to a standstill.
- **Acceleration limits**: every command is clamped to [−8.0, +3.0] m/s².
- **Set-speed ceiling in follow mode**: the command is limited so the ego speed
  cannot cross 30 m/s while following a faster lead (the gap opens instead,
  which is the correct ACC behavior).
- **Anti-windup**: conditional integration — the integral term stops
  accumulating while the output is saturated, preventing integral windup
  during full-throttle launch and long chase phases.
- **Non-negative speed**: the ego speed is floored at 0 m/s.

## 2. PID Tuning Methodology and Final Gains

### 2.1 Methodology

Gains were tuned offline by `tune_pid.py` (auto-tuning lives *only* there —
`simulation.py` loads the final gains from `tuning_results.yaml` at runtime):

1. **Full-run evaluation**: each candidate gain pair was scored by running the
   complete 150 s closed-loop simulation, so mode transitions and the harsh
   braking event at t ≈ 120 s were included in every evaluation.
2. **Penalty score** against the task targets: rise time ≥ 10 s, overshoot
   ≥ 5 %, speed steady-state error ≥ 0.5 m/s, distance steady-state error
   ≥ 2 m, minimum distance ≤ 10 m (the config's minimum-gap constraint, with
   the > 5 m task target as a fallback) each add a large penalty; small
   preference terms favor tighter settled tracking and lower control jerk.
3. **Coarse grid search** over both loops (kp ∈ [0.3, 3.0], ki ∈ [0, 0.3],
   kd ∈ [0, 0.2]), followed by iterative **local refinement** around the best
   grid point.
4. **Gain-range constraints** were enforced throughout: kp ∈ (0, 10),
   ki ∈ [0, 5), kd ∈ [0, 5).

Two design corrections came out of tuning:

- Emergency braking was changed from a kinematic "required deceleration"
  formula (which assumes constant lead speed) to full braking, after the
  original formula collided when the lead braked to a standstill at t ≈ 120 s.
- The desired-gap model uses `ego_speed * time_headway + min_distance`: the
  constant 10 m margin keeps the gap safely above the 10.0 m minimum-gap
  constraint through the abrupt lead-braking event (minimum gap 18.3 m),
  whereas a pure time-headway gap let the gap dip to ~8 m.

### 2.2 Final gains (`tuning_results.yaml`)

| Loop | kp | ki | kd | Used in |
|---|---|---|---|---|
| `pid_speed` | 3.5 | 0.0 | 0.02 | cruise mode (speed error → acceleration) |
| `pid_distance` | 4.4 | 2.18 | 0.0 | follow mode (distance error → acceleration) |

- Speed loop: high proportional gain gives a fast launch; the small derivative
  term damps the approach to the set speed. No integral term is needed — with
  no drag disturbance the P+D loop reaches zero steady-state error, and this
  avoids windup-induced overshoot.
- Distance loop: strong proportional + integral terms drive the gap error to
  zero in steady following (mean |error| 0.18 m); with anti-windup the integral
  does not accumulate during saturated chase phases.

## 3. Simulation Results and Performance Metrics

The final run (`simulation_results.csv`, 1501 rows, t = 0–150 s at 0.1 s):

| Metric | Target | Achieved | Result |
|---|---|---|---|
| Control duration | 150 s | 150 s | PASS |
| Speed rise time (10 % → 90 % of 30 m/s) | < 10 s | 8.0 s | PASS |
| Speed overshoot | < 5 % | 0.0 % | PASS |
| Speed steady-state error | < 0.5 m/s | 0.000 m/s | PASS |
| Distance steady-state error (settled follow, t = 40–80 s) | < 2 m | 0.18 m | PASS |
| Minimum distance | > 5 m (and ≥ 10 m gap constraint) | 18.22 m | PASS |
| Max |acceleration| | ≤ 8.0 m/s² | 8.0 m/s² (emergency braking only) | PASS |

### 3.1 Phase-by-phase behavior

| Phase | Time | Behavior |
|---|---|---|
| Launch / cruise | 0–30 s | Full-throttle launch at 3.0 m/s² (matching the expected output format), reaches 30 m/s with 8.0 s rise time, zero overshoot |
| Follow (entry) | 30–40 s | Lead detected at 52.1 m while ego at 30 m/s; ego brakes smoothly and settles onto the lead at 25 m/s, 47.4 m gap |
| Follow (settled) | 40–80 s | Tight time-headway + margin tracking, mean gap error 0.18 m |
| Chase | 80–110 s | Lead accelerates to 36.8 m/s (above the set speed); ACC correctly holds 30 m/s and lets the gap open to ~135 m — the large positive gap error here is by design, since ACC never exceeds the driver's set speed |
| Approach | 110–120 s | Lead decelerates to ~21 m/s; ego brakes from 30 m/s and re-tightens the gap |
| **Emergency** | 120.0–121.6 s | Lead brakes abruptly (20.6 → 0 m/s); TTC drops to 2.57 s < 3.0 s → full braking at −8.0 m/s² for 1.7 s. Minimum gap 18.22 m (≥ 10 m constraint, > 5 m target), no collision; minimum TTC 2.01 s |
| Recovery | 121.6–130 s | Follow mode resumes as TTC recovers; ego re-accelerates (limited to 3.0 m/s²) and tracks the lead |
| Cruise (return) | 130–150 s | Lead disappears; ego returns to 30 m/s and holds it with ~0 error |

### 3.2 Notes on the gap-error profile

The mean absolute gap error over the *entire* follow phase (t = 30–130 s) is
dominated by two unavoidable transients: the chase phase (t = 80–110 s), where
the lead travels faster than the set speed so the gap legitimately opens, and
the post-emergency recovery (t = 120–130 s), where the lead re-accelerates at
~7 m/s² — beyond the ego's 3.0 m/s² limit — so no controller can hold the
desired gap during the chase. Under steady-state conditions the controller
meets the < 2 m target with a large margin (0.18 m).

### 3.3 Artifacts

- `pid_controller.py` — `PIDController` (kp, ki, kd; `reset()`, `compute(error, dt)`)
- `acc_system.py` — `AdaptiveCruiseControl(config)`; `compute(ego_speed, lead_speed, distance, dt)` → `(acceleration_cmd, mode, distance_error)`
- `simulation.py` — simulation driver; gains loaded from `tuning_results.yaml`
- `tune_pid.py` — offline auto-tuning (grid search + local refinement)
- `tuning_results.yaml` — final tuned gains
- `simulation_results.csv` — 1501 rows: `time, ego_speed, acceleration_cmd, mode, distance_error, distance, ttc`
