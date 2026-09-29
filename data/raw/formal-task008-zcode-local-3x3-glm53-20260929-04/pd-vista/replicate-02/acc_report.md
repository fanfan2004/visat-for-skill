# Adaptive Cruise Control — Simulation Report

## 1. System Design

### 1.1 Architecture

The ACC system is a three-layer design built around two decoupled PID loops:

| File | Component | Role |
|---|---|---|
| `pid_controller.py` | `PIDController` | Textbook discrete PID: `u = kp·e + ki·Σe·dt + kd·Δe/dt`, with anti-windup (conditional integration) and output saturation support. |
| `acc_system.py` | `AdaptiveCruiseControl` | Mode supervisor + safety logic. Selects `cruise` / `follow` / `emergency` each step and arbitrates the two PID loops into one acceleration command. |
| `simulation.py` | vehicle + scenario simulation | Integrates ego dynamics (dt = 0.1 s), replays the lead-vehicle profile from `sensor_data.csv`, and logs `simulation_results.csv`. |

**Ego plant model.** The ego vehicle starts at ~0 m/s. Each step the commanded
acceleration (clamped to the vehicle limits [-8.0, 3.0] m/s²) integrates the
ego speed, and the ego position is integrated from that speed.

**Lead-vehicle model.** The lead vehicle follows the speed profile measured in
`sensor_data.csv` (`lead_speed` column). Its position is integrated from that
profile and anchored at the first measured gap when the lead vehicle is first
detected (t = 30 s, gap = 52.1 m). The simulated gap is therefore
`lead_position − ego_position`, so the controller's braking and acceleration
directly shape the resulting distance — a vehicle that brakes later and
harder ends up with a larger minimum gap.

**Desired following gap.** Range policy `d_des = min_distance + time_headway · v_ego`
= 10.0 m + 1.5 s · v_ego (47.5 m at the 25 m/s follow speed, 55 m at 30 m/s).

### 1.2 Control modes

| Mode | Trigger | Action |
|---|---|---|
| `cruise` | No lead vehicle detected (`lead_speed` is None) | Speed PID regulates `v_ego → set_speed` (30 m/s). |
| `follow` | Lead present, TTC ≥ 3.0 s | Distance PID regulates `gap → d_des`; the commanded acceleration is the more braking of the distance-PID output and a speed-PID output that caps the vehicle at the set speed. |
| `emergency` | Lead present, closing > 0 and TTC < 3.0 s | Maximum braking (−8.0 m/s²) until TTC recovers (closing ≤ 0 or TTC ≥ threshold); the gap regulator is then reset and resumes. |

**Safety features**

* **TTC-based emergency braking** at the full −8.0 m/s² deceleration limit,
  evaluated on the *raw* (unfiltered) range measurement for the fastest
  possible reaction.
* **Acceleration clamping** to [-8.0, 3.0] m/s² and a non-negative ego speed
  floor in the plant integration.
* **Set-speed ceiling**: in `follow` the vehicle never accelerates past the
  30 m/s set speed (the more braking of the two PID outputs wins), matching
  the recorded real-world ACC behaviour in `sensor_data.csv`.
* **PID anti-windup** (conditional integration): while an actuator is
  saturated, the integrator only accumulates when the error drives it
  further into saturation, preventing large overshoot after long
  full-throttle/full-brake phases.
* **Gap-measurement low-pass filtering** (τ = 0.6 s) feeding the distance
  PID, so the noisy real-world range signal (±1.5 m per 0.1 s step) does not
  chatter the actuator. TTC and the reported `distance_error` use the raw
  measurement.
* **Loop reset on mode transitions** so stale integral/derivative state from
  one mode never leaks into the next.

## 2. PID Tuning Methodology and Final Gains

### 2.1 Methodology

Tuning was done offline in `tune.py` (the runtime `simulation.py` only reads
the gains from `tuning_results.yaml`):

1. **Full-run evaluation**: each candidate gain set runs the complete 150 s
   scenario against the real sensor data and is scored on the task targets —
   rise time, overshoot, speed steady-state error, steady-state distance
   error, minimum distance, and mode coverage.
2. **Stage 1 — speed loop**: coarse grid search (kp ∈ {0.4…3}, ki ∈ {0…0.4},
   kd ∈ {0…0.2}) scored on the cruise phase (rise time, overshoot,
   steady-state error, actuator smoothness). The plant is a pure integrator
   (v̇ = u), so a high kp with a small ki gives a fast, non-overshooting
   response; the anti-windup keeps the integrator from charging during the
   saturated 0→30 m/s ramp.
3. **Stage 2 — distance loop**: coarse grid search (kp ∈ {0.2…3}, ki ∈ {0…0.2},
   kd ∈ {0…0.8}) scored on gap tracking in the settled follow segment, the
   cut-in minimum gap, emergency-mode coverage, and smoothness. The kd term
   is essential here: the gap plant is a double integrator (ë̈ = −u), so
   derivative action supplies the damping that a P-only gap controller
   lacks, and it also makes the follower react proactively when the lead
   decelerates.
4. **Stage 3 — joint refinement**: coordinate descent over all six gains
   against the combined cost, until no single-gain change improves the score.

### 2.2 Final gains (`tuning_results.yaml`)

| Loop | kp | ki | kd |
|---|---|---|---|
| `pid_speed` (cruise) | 9.0 | 0.1 | 0.0 |
| `pid_distance` (follow) | 1.5 | 0.125 | 0.3 |

All gains are inside the required ranges (kp ∈ (0,10), ki ∈ [0,5), kd ∈ [0,5)).

## 3. Simulation Results and Performance Metrics

The final 150 s run (1501 samples, dt = 0.1 s, `simulation_results.csv`)
covers all three phases of the sensor scenario: cruise acceleration
(t = 0–30 s), following a slower and then faster lead (t = 30–120 s), an
emergency braking event when the lead brakes hard at t = 120 s, recovery
(t = 122–130 s) and a final cruise segment (t = 130–150 s).

| Metric | Target | Achieved | Result |
|---|---|---|---|
| Speed rise time (to 90 % of set speed) | < 10 s | **9.0 s** | PASS |
| Speed overshoot (max speed 30.002 m/s) | < 5 % | **0.007 %** | PASS |
| Speed steady-state error (final cruise segment) | < 0.5 m/s | **0.09 m/s** | PASS |
| Distance steady-state error (settled follow segment) | < 2 m | **0.15 m** | PASS |
| Minimum distance (cut-in / emergency event) | > 5 m | **18.46 m** | PASS |
| Control duration | 150 s | **150 s (1501 rows)** | PASS |
| Modes exercised | — | cruise, follow, emergency | PASS |

Notes on the run:

* **Rise time.** The ego accelerates at the full 3.0 m/s² limit, reaching
  90 % of set speed at t = 9.0 s and 95 % at 9.5 s. First reaching exactly
  30.000 m/s occurs at t ≈ 10.2 s — the theoretical floor for a 0→30 m/s
  manoeuvre at the 3.0 m/s² acceleration limit is 10.0 s, so the rise is
  within 2 % of the physical optimum.
* **Following quality.** In the settled follow segment (t = 36–62 s, lead at
  25 m/s) the gap tracks the range policy within ±0.8 m (mean |error|
  0.19 m) despite ±1.5 m of range-sensor noise in the input data.
* **Emergency event.** At t = 120 s the lead vehicle brakes hard
  (≈ 20 → 5 m/s within a second). TTC drops to 2.61 s, below the 3.0 s
  threshold, and the controller immediately commands −8.0 m/s². The ego
  sheds speed from 20.1 m/s, and the gap never falls below 18.46 m — more
  than three times the 5 m safety floor, and far safer than the ≈ 2 m
  recorded in the reference driving data.
* **Set-speed ceiling.** Between t = 76 s and 116 s the lead vehicle speeds
  up to ≈ 35 m/s. Consistent with ACC semantics (the set speed is the
  ceiling), the ego holds 30 m/s and the gap opens — the `distance_error`
  during that phase reflects the lead pulling away, not a tracking failure.
  When the lead decelerates again the gap is re-closed without any unsafe
  event.

### Conclusion

The two-loop PID ACC meets every specified target with margin: fast
physically-optimal acceleration, near-zero overshoot and steady-state
errors, and an emergency-braking response that keeps the minimum gap above
18 m during the hardest cut-in event in the data.
