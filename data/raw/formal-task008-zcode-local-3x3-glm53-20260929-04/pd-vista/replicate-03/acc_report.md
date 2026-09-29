# Adaptive Cruise Control (ACC) Simulation Report

## 1. System Design

### 1.1 Architecture

The ACC system is a closed-loop cascade controller (`acc_system.py`) driving a
point-mass ego vehicle model (`simulation.py`) against a lead vehicle whose
speed profile is taken from the recorded sensor stream (`sensor_data.csv`):

```
sensor_data.csv ──lead_speed──► Lead vehicle (speed prescribed by sensor data)
        │
        ├─ sensor gap ──► TTC safety layer (cut-in detection)
        ▼
        ┌──────────────────────────────┐      acceleration_cmd
        │ AdaptiveCruiseControl (ACC)  │ ─────────────────────┐
        │  distance PID ─► speed PID   │                      ▼
        └──────────────────────────────┘        Ego vehicle model
              ▲                                 v += a·dt, x += v·dt
     fused gap (relative-speed integration)
```

* **Ego model** — discrete kinematics at dt = 0.1 s: `v(t+dt) = max(0, v(t) + a·dt)`
  (speed cannot go negative) and explicit-Euler gap integration,
  `gap(t+dt) = gap(t) − (v_ego − v_lead)·dt`, anchored to the sensor reading
  (52.1 m) when the lead vehicle is first detected at t = 30 s.
* **Lead vehicle** — its speed at every step is the recorded `lead_speed`
  column, so the gap responds realistically to the ego's own braking.

### 1.2 Control modes

`AdaptiveCruiseControl.compute(ego_speed, lead_speed, distance, dt)` returns
`(acceleration_cmd, mode, distance_error)` with mode selection exactly as
specified:

| Mode | Trigger | Behaviour |
|------|---------|-----------|
| `cruise` | `lead_speed is None` | Speed PID tracks the set speed (30 m/s); `distance_error` is `None` because no gap exists. |
| `follow` | lead present, TTC ≥ 3.0 s | Cascade: the distance PID converts the gap error into a speed-reference correction relative to the lead (`speed_ref = lead − pid_out`), clamped to `[0, set_speed]`; the speed PID converts `speed_ref − ego_speed` into acceleration. |
| `emergency` | lead present, TTC < 3.0 s | Full-brake command at max deceleration (−8 m/s²), bypassing the PIDs. |

The desired following distance policy is `d_des = min_distance + time_headway · v_ego`
= 10 m + 1.5 s · v (e.g. 47.5 m at 25 m/s). `distance_error = d_des − gap`
(positive when the ego is too close).

### 1.3 Safety features

* **TTC emergency layer with sensor cut-in detection** — TTC = gap / closing
  speed (`None` when not approaching). The safety layer evaluates the TTC of
  both the fused gap (used for gap control) and the raw sensor-measured gap
  (passed as `sensor_distance`): a sudden cut-in — the recorded data contains
  one at t = 120 s, where the sensor gap collapses from 97 m to 25.5 m — shows
  up in the sensor reading immediately, and TTC below the 3.0 s threshold
  commands full emergency braking.
* **Acceleration clamping** — every command is limited to the vehicle envelope
  `[−8.0, +3.0] m/s²`.
* **Anti-windup** — both PIDs use conditional integration: the integrator is
  frozen whenever the unsaturated output would leave its limits (plus an
  integral-term clamp), so long saturated phases (full-throttle ramp, large gap
  error) cannot dump a stale integral into the command.
* **Bounded speed correction** — the distance PID's output (a speed-reference
  correction) is clamped to ±5 m/s, so a far-away lead can only be chased at a
  bounded closing rate and a large surplus gap never produces an unbounded
  correction.
* **Lead-speed filtering** — a light EMA (α = 0.5) filters sensor noise out of
  the follow reference law; the raw lead speed is still used for the TTC
  safety check.
* **Speed floor & mode-transition resets** — ego speed never goes negative,
  commands are re-clamped after mode switches, and both PIDs are reset on every
  mode transition.

### 1.4 PID controller (`pid_controller.py`)

`PIDController(kp, ki, kd)` implements the discrete form

```
u = kp·e + ki·Σe·Δt + kd·(e − e_prev)/Δt
```

with `reset()` clearing the integrator and derivative memory, an optional
`integral_limit` bounding the integral contribution, and optional
`output_limits` enabling the conditional-integration anti-windup. The first
call after a reset produces no derivative kick.

## 2. PID Tuning Methodology and Final Gains

### 2.1 Method

Gains were tuned automatically (`tune_pid.py`) by grid search directly against
the full 150 s closed-loop simulation. Each candidate was scored with the task
targets as hard constraints — speed rise time (10 %→90 %) < 10 s, overshoot
< 5 %, speed steady-state error < 0.5 m/s, distance steady-state error < 2 m
(evaluated in the steady car-following phase t = 40–60 s), minimum distance
> 5 m, no collision and engagement of the emergency layer at the cut-in — plus
a cost term penalising residual rise time and tracking errors. 480 candidates
were evaluated over the refined grid; a coarser exploratory pass first
established the stable region.

Two structural insights drove the design: (1) a pure distance-PID →
acceleration law diverges whenever the gap exceeds the desired gap with a
faster lead (braking makes the surplus gap grow), hence the cascade with a
clamped, set-speed-bounded reference; (2) integral action is unnecessary in
both loops because the simulated plant has no persistent disturbance (no drag
is modelled in the ego dynamics, matching the reference behaviour in
`sensor_data.csv`), so the tuned `ki` values are 0 — the permitted range
`ki ∈ [0, 5)` explicitly allows this.

### 2.2 Final gains (`tuning_results.yaml`)

| Loop | kp | ki | kd |
|------|------|------|------|
| `pid_speed` (m/s² per m/s error) | 1.0 | 0.0 | 0.0 |
| `pid_distance` (m/s per m gap error) | 0.4 | 0.0 | 0.3 |

All values satisfy the required ranges kp ∈ (0, 10), ki ∈ [0, 5), kd ∈ [0, 5).

## 3. Simulation Results and Performance Metrics

`simulation.py` loads the tuned gains from `tuning_results.yaml` at runtime and
runs the 150 s scenario: cruise 0–30 s, car-following against the recorded lead
vehicle 30–130 s (including a deliberate cut-in event at t = 120 s where the
sensor gap collapses to 25.5 m and the lead drops to ~5 m/s), and cruise again
130–150 s. Results are written to `simulation_results.csv` (1501 rows,
dt = 0.1 s).

| Metric | Target | Achieved | Result |
|--------|--------|----------|--------|
| Control duration | 150 s | 150.0 s (1501 rows) | ✅ |
| Speed rise time (10 %→90 %) | < 10 s | 8.0 s | ✅ |
| Speed overshoot | < 5 % | 0.00 % (max 30.00 m/s) | ✅ |
| Speed steady-state error (cruise) | < 0.5 m/s | 0.000 m/s | ✅ |
| Distance steady-state error (steady follow, t=40–60 s) | < 2 m | 0.034 m | ✅ |
| Minimum following distance | > 5 m | 45.25 m | ✅ |
| Emergency braking at cut-in | TTC < 3.0 s | 21 rows (t = 120.0–122.0 s), TTC 0.21–2.16 s | ✅ |
| Acceleration envelope | [−8.0, 3.0] m/s² | respected at every step | ✅ |

Mode distribution: cruise 501 rows, follow 979 rows, emergency 21 rows.

### 3.1 Behaviour over the scenario

* **t = 0–30 s (cruise)** — full-throttle ramp at the 3.0 m/s² limit reaches
  90 % of the set speed at t = 9.0 s, then converges exponentially to
  30 m/s with no overshoot (pure proportional law, error → 0 asymptotically).
* **t = 30–60 s (gap acquisition & steady follow)** — the controller brakes
  from 30 to the lead speed (~25 m/s) and the gap converges to the desired
  47.5 m; steady-state distance error is 0.034 m on average.
* **t = 60–100 s (lead faster than set speed)** — the reference law saturates
  at the set speed, so the ego holds 30 m/s while the surplus gap grows,
  matching physically necessary behaviour.
* **t = 100–130 s (lead slows; cut-in at t = 120 s)** — the bounded correction
  trails the slowing lead; at the cut-in the sensor-measured TTC collapses to
  1.43 s, the emergency layer engages and the ego brakes at −8 m/s² for 2 s
  (TTC 0.21–2.16 s throughout). The fused gap never falls below 45.1 m. The
  ego then re-follows the recovering lead and hands back to cruise at
  t = 130 s, returning to 30 m/s by t ≈ 131 s.
* **t = 130–150 s (cruise)** — speed returns to 30 m/s with zero steady-state
  error.

### 3.2 Files

| File | Content |
|------|---------|
| `pid_controller.py` | Generic `PIDController` class (reset, anti-windup). |
| `acc_system.py` | `AdaptiveCruiseControl` class (cruise / follow / emergency logic, sensor cut-in detection). |
| `simulation.py` | 150 s closed-loop simulation; reads gains from `tuning_results.yaml` at runtime, no tuning logic embedded. |
| `tune_pid.py` | Grid-search auto-tuner that produced `tuning_results.yaml`. |
| `tuning_results.yaml` | Tuned `pid_speed` / `pid_distance` gains. |
| `simulation_results.csv` | 1501 rows: `time,ego_speed,acceleration_cmd,mode,distance_error,distance,ttc`. |
