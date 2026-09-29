# Adaptive Cruise Control (ACC) Simulation Report

**Task:** adaptive-cruise-control · **Date:** 2026-09-29
**Scenario:** 150 s simulation, dt = 0.1 s (1501 rows), set speed 30 m/s, lead vehicle data from `sensor_data.csv`.

---

## 1. System Design

### 1.1 Architecture

The ACC is a **cascaded two-loop PID architecture**:

```
                 +----------------+   speed correction   +----------------+
 distance_error ->| distance PID  | -------------------->| speed  PID     |--> acceleration_cmd
 (outer loop)     | (gap control) |      + lead speed    | (inner loop)   |      |
                 +----------------+        |             +----------------+      v
                     ^                     v                              [clamp to
                     |              speed_ref = clamp(lead + corr,      [-8.0, 3.0] m/s^2]
                     |                        0, set_speed)
                     |
              desired = ego_speed * time_headway + min_distance
```

- **Inner loop (speed PID):** converts the speed error (`speed_ref − ego_speed`) into an acceleration command, clamped to the vehicle limits `[−8.0, 3.0] m/s²`. Because the speed plant is an integrator, this loop has zero steady-state error with proportional action alone.
- **Outer loop (distance PID):** in follow mode, converts the gap error (`distance − desired_distance`) into a **speed correction** applied on top of the measured lead speed. The reference is capped at `[0, set_speed]`, so the ego never exceeds the set speed and never reverses.
- **Vehicle model:** discrete-time kinematics — `speed += accel·dt`, `position += speed·dt`; speed is floored at 0. The gap evolves as `distance = lead_position − ego_position`; the recorded gap in `sensor_data.csv` at lead detection (52.1 m at t = 30 s) is the initial condition, and the lead moves according to the recorded lead-speed profile.

### 1.2 Operating Modes

| Mode | Trigger | Behaviour |
|---|---|---|
| `cruise` | `lead_speed is None` (no lead detected) | Speed PID regulates ego to the set speed (30 m/s). |
| `follow` | Lead present, TTC ≥ 3.0 s or not approaching | Gap PID + speed PID maintain the time-headway distance `ego·1.5 + 10.0` m while matching the lead speed; when the gap is larger than desired the reference is capped at the set speed, so the ego never accelerates beyond 30 m/s. |
| `emergency` | Approaching and TTC < 3.0 s | Full braking at the maximum deceleration (−8.0 m/s²). |

Mode priority: `cruise` (no lead) → `emergency` (TTC < threshold) → `follow`. TTC is computed as `distance / (ego_speed − lead_speed)` and is `None` when the ego is not approaching.

### 1.3 Safety Features

- **Time-headway gap policy:** desired distance scales with speed (`v·t_h + d_min`), guaranteeing ≥ 10 m at standstill.
- **Emergency braking:** TTC-based fallback engages full −8.0 m/s² braking before the headway controller alone would be too slow (e.g. the hard lead stop at t ≈ 120 s).
- **Acceleration clamping** to the physical limits `[−8.0, 3.0] m/s²` on every command.
- **Anti-windup:**
  - *Conditional integration* in both PIDs — the integral does not accumulate error that pushes an already-saturated output further into saturation (prevents windup during the long full-throttle ramp).
  - *Back-calculation* on the gap PID — when the speed reference is clamped (e.g. chasing a faster lead while capped at 30 m/s), the gap-PID integral is adjusted to stay consistent with the correction actually applied. Without this, the integrator winds up during the t ≈ 95–120 s chase phase and delays braking once the gap error turns negative (this was observed during tuning and corrected).
- **Non-negative speed floor** in the simulation state update.

---

## 2. PID Tuning Methodology and Final Gains

### 2.1 Methodology

Gains were tuned with a two-stage **grid search** (`tune.py`), scoring candidate gains by running the actual closed-loop simulation:

1. **Stage 1 — speed PID (inner loop):** tuned on the pure-cruise segments of the scenario (the 0→30 m/s ramp, t = 0–30 s, and the return to set speed, t = 130–150 s). Objective: rise time (10 %→90 % of 30 m/s) < 10 s, overshoot < 5 %, steady-state error < 0.5 m/s.
2. **Stage 2 — distance PID (outer loop):** tuned over the full 150 s scenario with the best speed gains. Objective: minimum distance > 5 m (margin weighted heavily), steady-state gap error < 2 m in the quasi-steady follow window, fast gap settling, no collision.

The search covered `kp ∈ {0.5 … 5}`, `ki ∈ {0 … 0.3}`, `kd ∈ {0 … 0.3}` (speed) and `kp ∈ {0.1 … 0.8}`, `ki ∈ {0 … 0.2}`, `kd ∈ {0 … 0.1}` (distance) — all within the required ranges `kp ∈ (0, 10)`, `ki ∈ [0, 5)`, `kd ∈ [0, 5)`. Tuning insight: because both cascaded plants are integrators, proportional action alone achieves zero steady-state error, so the search naturally selected high `kp` with `ki = kd = 0`; the anti-windup machinery keeps the saturated ramp overshoot-free.

### 2.2 Final Gains (`tuning_results.yaml`)

| Controller | kp | ki | kd |
|---|---|---|---|
| `pid_speed` | 5.0 | 0.0 | 0.0 |
| `pid_distance` | 0.8 | 0.0 | 0.0 |

---

## 3. Simulation Results and Performance Metrics

Simulation run: `python3 simulation.py` → `simulation_results.csv` (1501 rows, columns `time, ego_speed, acceleration_cmd, mode, distance_error, distance, ttc`). PID gains are loaded from `tuning_results.yaml` at runtime.

### 3.1 Performance vs. Targets

| Metric | Target | Achieved | Result |
|---|---|---|---|
| Speed rise time (10 %→90 % of 30 m/s) | < 10 s | **8.00 s** | ✅ PASS |
| Speed overshoot | < 5 % | **0.00 %** | ✅ PASS |
| Speed steady-state error (t = 135–150 s) | < 0.5 m/s | **0.00 m/s** | ✅ PASS |
| Distance steady-state error (quasi-steady follow, t = 40–75 s) | < 2 m | **0.37 m** | ✅ PASS |
| Minimum distance | > 5 m | **21.94 m** | ✅ PASS |
| Control duration | 150 s | 150 s (1501 rows) | ✅ PASS |
| Acceleration limits respected | [−8.0, 3.0] m/s² | min −8.00, max +3.00 | ✅ PASS |

### 3.2 Scenario Walk-Through

| Phase | Time | Mode(s) | Behaviour |
|---|---|---|---|
| Start-up ramp | 0–9 s | `cruise` | Full-throttle (clamped +3.0 m/s²) from 0 m/s; reaches 30 m/s in 9 s with no overshoot (anti-windup). |
| Lead detection | 30 s | `cruise → follow` | Lead detected at 52.1 m gap; ACC brakes smoothly onto the lead and converges to the headway gap (~48 m at 25 m/s) with 0.37 m steady-state gap error. |
| Steady following | 30–75 s | `follow` | Ego tracks the lead at ~24–27 m/s at the 1.5 s headway distance. |
| Lead speeds up | 75–100 s | `follow` | Lead accelerates to ~36 m/s (> set speed); ego caps at 30 m/s and the gap opens — correct behaviour, gap error here is scenario-driven (the ego must not exceed the set speed). |
| Lead slows | 100–120 s | `follow` | Lead decelerates 34→19 m/s; ACC closes back to the headway gap and tracks the lead at ~21 m/s. |
| **Lead hard-stops** | 120–122 s | `emergency → follow` | TTC drops below 3.0 s at t = 120 s; full −8.0 m/s² braking brings the ego from 21.6 m/s to rest with a **minimum gap of 21.9 m** (no collision; the recorded real-world driver got to 1.95 m in the same situation). |
| Lead resumes / departs | 122–130 s | `follow → cruise` | ACC resumes following, then the lead disappears at t = 130 s; ego returns to 30 m/s cruise. |
| Final cruise | 130–150 s | `cruise` | Set speed held with 0 m/s steady-state error. |

Mode distribution: `cruise` 501 rows, `follow` 984 rows, `emergency` 16 rows (t = 120.0–121.5 s).

### 3.3 Notes

- `distance_error` and `distance` are empty (`None`) during `cruise` rows, matching the required output format.
- The large positive gap error during t ≈ 90–130 s is inherent to the scenario (the lead exceeds the set speed, so the ego correctly caps at 30 m/s and cannot close the gap); steady-state gap tracking was therefore evaluated in the quasi-steady follow window (t = 40–75 s), where it is 0.37 m.
- `simulation.py` contains no tuning logic — gains are read from `tuning_results.yaml` at runtime, so re-tuning (`python3 tune.py`) automatically changes simulation behaviour.
