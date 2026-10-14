# unit-commitment-data-modeling

---
name: unit-commitment-data-modeling
description: Use for parsing structured unit commitment input data from JSON, CSV, benchmark cases, spreadsheets, databases, or nested tables; finding fields for time periods, resources, load, reserve, generator limits, initial conditions, startup data, renewable availability, and production costs without assuming one source-specific schema.
---

# Unit Commitment Structured Data Parsing

Use this skill when a unit commitment task provides structured data and you need to map fields into UC concepts. The source may be JSON, CSV, spreadsheets, database tables, or nested dictionaries. The prompt and schema are the source of truth; do not assume one benchmark or package.

## Parsing Workflow

1. Load data with structured parsers: JSON as objects, CSV/sheets as tables, databases as query results.
2. Inspect schema: top-level keys, tables/sheets, resource groups, time-series fields, cost curves, startup tiers, and initial-condition fields.
3. Identify the time axis: number of periods, labels, duration, and report convention.
4. Identify resource sets: thermal, renewable, storage, imports, zones, reserve products, or network objects.
5. Normalize fields into arrays/tables with explicit shapes.
6. Preserve original names and source ordering for final reports.
7. Run parser-level checks before modeling.

## Map Concepts, Not Names

Different sources use different names. Map by meaning, units, shape, and context.

| UC concept | Look for |
| --- | --- |
| Horizon | periods, hours, timestamps, interval count |
| Demand | load, system demand, net load, zone load |
| Reserve requirement | spinning, operating, contingency, regulation reserve |
| Resource sets | thermal, renewable, storage, import/export |
| Commitment status | on/off, online, active, unit status |
| Output limits | minimum stable output, maximum output, availability |
| Ramping | ramp up/down, startup capability, shutdown capability |
| Minimum up/down | required duration after start/stop |
| Initial conditions | initial status, initial output, time already on/off |
| Must-run | forced online, fixed status |
| Startup data | fixed costs or tiers by prior offline duration |
| Production cost | linear coefficients, heat rate, piecewise or total-cost curves |
| Renewable availability | hourly min/max output or forecast bounds |

## Common Data Shapes

- **Scalar by resource:** min up/down, ramp rates, startup ramp, must-run.
- **Time series by system/zone:** demand and reserve requirement.
- **Time series by resource:** renewable availability or outage status.
- **Curve/tier tables:** startup costs and production-cost breakpoints.
- **Nested resource objects:** generator-specific limits, status, and costs.

Normalize into a small representation:

```python
case = {
    "periods": periods,                  # length T
    "thermal_names": thermal_names,      # length G, source order
    "renewable_names": renewable_names,  # length R, source order
    "demand": demand,                    # shape (T,)
    "reserve_requirement": reserve,       # shape (T,)
    "thermal": thermal_params,
    "renewable_min": renewable_min,       # shape (R, T)
    "renewable_max": renewable_max,       # shape (R, T)
}
```

## Time, Ordering, And Units

- Use the input horizon as authoritative.
- Preserve source period order.
- Keep zero-based internal indexes separate from one-based/timestamped report labels.
- Verify every time-series length equals `T`.
- Preserve resource order unless the prompt requires sorting.
- Treat resource IDs as opaque strings.
- Keep thermal and renewable sets separate when constraints differ.
- Check power units, period duration, ramp-rate units, and cost units before converting anything.

Basic checks:

```python
assert len(demand) == T
assert len(reserve_requirement) == T
for r in renewable_resources:
    assert len(r["min"]) == T
    assert len(r["max"]) == T
```

## Production Convention

Many UC models use output above minimum internally, while reports often require actual MW.

```python
actual_output = pmin * commitment + output_above_min
output_above_min = actual_output - pmin * commitment
```

Pick one internal convention and convert carefully for reporting, ramping, reserve deliverability, and cost.

## Startup Tiers

Startup tiers are usually keyed by prior offline duration. Parse thresholds and costs without assuming order.

```python
def choose_startup_tier(tiers, prior_offline_duration):
    tiers = sorted(tiers, key=lambda x: x["lag"])
    chosen = tiers[0]
    for tier in tiers:
        if tier["lag"] <= prior_offline_duration:
            chosen = tier
        else:
            break
    return chosen
```

Keep prior offline duration consistent with initial status and transition timing.

## Cost Curves

Identify whether points are total cost, marginal cost, incremental segment cost, or heat-rate data. For total-cost breakpoints:

```python
def interpolate_total_cost(points, output_mw):
    pts = sorted((float(p["mw"]), float(p["cost"])) for p in points)
    if output_mw <= pts[0][0]:
        return pts[0][1]
    if output_mw >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= output_mw <= x1:
            a = (output_mw - x0) / (x1 - x0)
            return y0 + a * (y1 - y0)
    raise ValueError("output outside cost curve")
```

If the first point is at minimum output, it may represent online minimum-output cost. Do not invent additional no-load or shutdown costs unless provided.

## Renewables

- Parse hourly minimum and maximum output.
- If min equals max, output is fixed in that period.
- If curtailment is allowed, output can be anywhere between min and max.
- Do not count renewable headroom as spinning reserve unless explicitly allowed.
- Renewable cost is zero unless the task/data says otherwise.

## Parser-Level Validation

Before solving, check:

```python
assert np.all(np.isfinite(demand))
assert np.all(np.isfinite(reserve_requirement))
assert np.all(thermal_pmin <= thermal_pmax)
assert np.all(renewable_min <= renewable_max)
assert all(len(curve) >= 2 for curve in production_curves.values())
assert all(len(tiers) >= 1 for tiers in startup_tiers.values())
```

Also check missing required fields, duplicate IDs, mismatched lengths, negative impossible limits, repeated cost points, nonmonotone startup lags, and inconsistent initial status/output.

## Common Mistakes

- Hard-coding a familiar schema instead of inspecting the data.
- Losing ordering when converting dictionaries or tables into arrays.
- Joining tables on the wrong key or duplicating resources.
- Confusing total output with output above minimum.
- Confusing reserve, capacity, availability, and dispatch.
- Treating every cost curve as marginal cost.
- Ignoring startup tier lags or initial offline duration.
- Assuming renewable maximum output must always be used.


# unit-commitment-operating-rules

---
name: unit-commitment-operating-rules
description: Use for day-ahead or multi-period unit commitment problems, including thermal on/off schedules, dispatch, startup/shutdown logic, minimum up/down time, ramping, spinning reserve deliverability, renewable curtailment, operating-cost accounting, and independent feasibility checks for power-system operations schedules.
---

# Unit Commitment Operating Rules

Use this skill when a task asks for a day-ahead or multi-period unit commitment schedule with generators, load, reserves, operating constraints, and cost tradeoffs.

This is a reusable UC operating guide. It gives formulation and validation patterns, not a complete task-specific mathematical model.

## UC In One Paragraph

Unit commitment decides which generators are online over time, when they start or stop, how much they produce, and how much reserve they can physically provide. It is harder than hourly economic dispatch because startup/shutdown decisions, ramping, minimum up/down time, reserve deliverability, initial conditions, and cost curves couple one period to the next.

## Keep These Concepts Separate

For each thermal unit `g` and period `t`:

```python
u[g, t]      # commitment/on status, binary
start[g, t]  # startup transition, binary
stop[g, t]   # shutdown transition, binary
p[g, t]      # production variable: know whether actual MW or above-minimum MW
r[g, t]      # scheduled reserve
```

Reports often require actual MW output. Many UC models internally use output above minimum:

```python
actual_output = pmin[g] * u[g, t] + p_above_min[g, t]
p_above_min = actual_output - pmin[g] * u[g, t]
```

Do not mix these conventions in ramping, reserve, cost, or reporting.

## Core Feasibility Checks

Before reporting a schedule, independently verify:

- every resource appears exactly once and all time-series have length `T`;
- commitment, startup, and shutdown are binary;
- offline thermal units have zero production and zero reserve;
- online thermal units respect min/max output;
- must-run units are online when required;
- startup/shutdown indicators match commitment transitions;
- demand balance holds in every period;
- renewable output stays within period-specific bounds;
- scheduled reserve meets the system requirement;
- reserve is deliverable under headroom, startup/shutdown capability, and ramp limits;
- minimum up/down time and initial conditions are respected;
- cost and summary fields are recomputed from arrays.

## Transition Logic

Link startup/shutdown to commitment and the initial state:

```python
prev_u = initial_on[g] if t == 0 else u[g, t - 1]
u[g, t] - prev_u == start[g, t] - stop[g, t]
start[g, t] + stop[g, t] <= 1
```

Equivalent validation pattern:

```python
prev_on = initial_on[g]
for t in range(T):
    assert start[g, t] == int(u[g, t] == 1 and prev_on == 0)
    assert stop[g, t] == int(u[g, t] == 0 and prev_on == 1)
    prev_on = u[g, t]
```

## Capacity And Offline Zeroes

For actual-MW production:

```python
pmin[g] * u[g, t] <= production[g, t] <= pmax[g] * u[g, t]
0 <= reserve[g, t]
```

For above-minimum production:

```python
cap = pmax[g] - pmin[g]
0 <= p_above_min[g, t] <= cap * u[g, t]
0 <= reserve[g, t]
```

If `u[g, t] == 0`, both production and reserve must be zero.

## Demand, Renewables, And System Reserve

Use the task's system/zone/network convention. For a single-zone system:

```python
thermal_gen = sum(actual_thermal[g, t] for g in thermal_units)
renew_gen = sum(renewable_output[r, t] for r in renewable_units)
assert abs(thermal_gen + renew_gen - demand[t]) <= tol
assert sum(reserve[g, t] for g in thermal_units) >= reserve_requirement[t] - tol
```

Renewables:

```python
renewable_min[r, t] <= renewable_output[r, t] <= renewable_max[r, t]
```

If min equals max, output is fixed. If curtailment is allowed, output may be below max. Do not count renewable headroom as spinning reserve unless the prompt explicitly allows it.

## Reserve Deliverability

Reserve is not just unused nameplate capacity. Production and reserve compete for the same physical capability, and reserve must be deployable.

Headroom-only checks are too weak:

```python
# Not enough by itself:
reserve[g, t] <= pmax[g] - production[g, t]
reserve[g, t] <= ramp_up[g]
```

Use joint production-plus-reserve checks. With actual-MW production:

```python
production[g, t] + reserve[g, t] <= pmax[g] * u[g, t]
```

With above-minimum production:

```python
p_above_min[g, t] + reserve[g, t] <= (pmax[g] - pmin[g]) * u[g, t]
```

Startup capability can tighten the startup period. If `startup_limit` is maximum total output during startup:

```python
if start[g, t] == 1:
    production[g, t] + reserve[g, t] <= startup_limit[g]
```

A linear above-minimum pattern is:

```python
startup_reduction = max(pmax[g] - startup_limit[g], 0.0)
p_above_min[g, t] + reserve[g, t] <= (
    (pmax[g] - pmin[g]) * u[g, t]
    - startup_reduction * start[g, t]
)
```

Apply analogous shutdown-period or pre-shutdown capability rules when the data and prompt require them.

## Ramping

Use initial output/status for the first period. When reserve must be deliverable, ramp-up usually applies to production plus reserve:

```python
previous = initial_above_min[g] if t == 0 else p_above_min[g, t - 1]
p_above_min[g, t] + reserve[g, t] - previous <= ramp_up[g]
previous - p_above_min[g, t] <= ramp_down[g]
```

If your model uses actual production, convert consistently before applying above-minimum ramp checks. Recheck ramping after any dispatch or repair step.

## Minimum Up/Down Time

Minimum up/down constraints are time-window constraints triggered by starts/stops. Validation pattern:

```python
if start[g, t] == 1:
    for tau in range(t, min(T, t + min_up[g])):
        assert u[g, tau] == 1

if stop[g, t] == 1:
    for tau in range(t, min(T, t + min_down[g])):
        assert u[g, tau] == 0
```

Account for pre-horizon time already on/off. Follow the prompt on whether post-horizon obligations are enforced.

## Startup Costs

Startup cost may depend on prior offline duration. A common tier rule is largest lag not exceeding prior offline duration:

```python
def choose_startup_cost(tiers, offline_duration):
    tiers = sorted(tiers, key=lambda z: z["lag"])
    chosen = tiers[0]
    for tier in tiers:
        if tier["lag"] <= offline_duration:
            chosen = tier
        else:
            break
    return chosen["cost"]
```

Update offline duration from initial status and the commitment trajectory. Be careful: duration should describe time offline before the startup period.

## Production Costs

Use only cost components present in the data or required by the prompt. Do not invent no-load, reserve, curtailment, shutdown, or ramping costs.

For total-cost breakpoints:

```python
def total_cost_from_curve(points, output_mw):
    pts = sorted((p["mw"], p["cost"]) for p in points)
    if output_mw <= pts[0][0]:
        return pts[0][1]
    if output_mw >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= output_mw <= x1:
            return y0 + (output_mw - x0) * (y1 - y0) / (x1 - x0)
    raise ValueError("output outside curve")
```

If the first point is at minimum output, its cost may represent online minimum-output cost. Do not add another fixed online cost unless the data says so.

## Implementation Workflow

1. Parse and normalize resource/time arrays.
2. Choose actual-output or above-minimum internal variables and stick to it.
3. Include hard feasibility constraints before optimizing cost.
4. Extract the solution into the report convention.
5. Run independent validation on extracted arrays.
6. Recompute costs and summaries from arrays.
7. Write `"pass"` checks only after validation passes.

## Common Pitfalls

- Treating UC as independent hourly dispatch.
- Counting reserve from offline units or renewable headroom.
- Checking reserve headroom but forgetting startup or ramp deliverability.
- Ignoring initial output in first-period ramping.
- Ignoring initial on/off duration in minimum up/down constraints.
- Trusting a repair LP that omits a constraint family.
- Hard-coding `"pass"` fields before validation.


# milp-solver-workflow

---
name: milp-solver-workflow
description: Use for formulating, solving, debugging, and validating mixed-integer linear optimization models with open-source solvers, including variable indexing, sparse constraints, linearized costs, solver limits, MIP gaps, incumbent extraction, numerical tolerances, and deterministic output reporting.
---

# MILP Solver Workflow

Use this skill for binary/integer decisions, linear constraints, and linear or piecewise-linear objectives. It is useful for time-expanded scheduling models with many repeated resource-period constraints.

This is a workflow and implementation guide, not a complete formulation for any one task.

## Workflow

1. Parse and normalize data into ordered arrays.
2. Define decision states before coding: status, transitions, continuous quantities, slacks, segments, tiers.
3. Build a deterministic variable map.
4. Add constraints family by family: bounds, linking, balance, time coupling, capacity/ramp limits, cost logic.
5. Solve with an available open-source MILP solver.
6. Extract a candidate solution, rounding binaries only if near integral.
7. Convert internal variables into the report convention.
8. Independently validate extracted arrays.
9. Recompute objective and summaries from extracted arrays.
10. Write final output only after validation passes.

## Variable Map Pattern

Use helper functions or dictionaries, not scattered index arithmetic.

```python
offset = {}
n = 0

def alloc(name, shape, lb=0.0, ub=float("inf"), integer=False):
    global n
    size = int(np.prod(shape))
    idx = np.arange(n, n + size).reshape(shape)
    offset[name] = idx
    n += size
    return idx

u = alloc("commitment", (G, T), lb=0, ub=1, integer=True)
start = alloc("startup", (G, T), lb=0, ub=1, integer=True)
dispatch = alloc("dispatch", (G, T), lb=0)
reserve = alloc("reserve", (G, T), lb=0)
```

Keep variable ownership obvious: type, resource, period, and optional segment/tier.

## Sparse Constraint Pattern

Use sparse rows for large time-expanded models:

```python
rows, cols, vals = [], [], []
lb, ub = [], []
row = 0

def add_row(terms, lo, hi):
    global row
    for j, a in terms:
        if abs(a) > 0:
            rows.append(row)
            cols.append(j)
            vals.append(float(a))
    lb.append(float(lo))
    ub.append(float(hi))
    row += 1
```

Use equality rows for conservation/linking and upper/lower bound rows for capacity, reserve, ramping, timing, and logic.

## Sign-Safe Encoding

Write the intended inequality first, then move variable terms to the left-hand side.

```python
# Intended: x + y <= cap * u - reduction * start
# Row form: x + y - cap*u + reduction*start <= 0
add_row(
    [(x, 1.0), (y, 1.0), (u, -cap), (start, reduction)],
    lo=-INF,
    hi=0.0,
)
```

For non-obvious rows, test a tiny hand case. Example: set `u=1`, `start=1` and check the remaining capacity equals the intended startup capability; set `u=1`, `start=0` and check normal capacity returns.

## Match Model Rows To Validation

Keep a checklist linking each model constraint family to a validation check:

```text
transition linking      -> startup/shutdown match status
online capacity         -> offline zeroes and min/max output
joint reserve capacity  -> production plus reserve fits capability
ramp deliverability     -> reserve can be deployed within ramp-up
minimum durations       -> starts/stops imply required status windows
balance equations       -> demand/load/inventory conservation
cost-curve logic        -> recomputed objective matches report
```

If validation checks something not in the model, the solver may produce an invalid report. If the model has a constraint not validated after extraction, conversion bugs can slip through.

## Piecewise-Linear Costs

Identify the curve convention before modeling:

- total cost at output breakpoints;
- marginal or incremental segment cost;
- heat-rate curve;
- first point as minimum-output or no-load-like cost.

For segment variables, constrain segment quantities to their widths and sum them to the modeled production quantity. Use slopes only when the data represents total-cost breakpoints or incremental segment costs consistently.

## Open-Source Solver Use

HiGHS through SciPy is a common default when available:

```python
from scipy.optimize import milp, LinearConstraint, Bounds

result = milp(
    c=c,
    integrality=integrality,
    bounds=Bounds(lb, ub),
    constraints=constraints,
    options={"time_limit": 600.0, "mip_rel_gap": 0.01, "disp": False},
)

if result.x is None:
    raise RuntimeError(f"No incumbent: status={result.status}, message={result.message}")
```

Capture status, objective, incumbent values, and any reliable gap/bound. A time-limit status can be useful if a feasible incumbent exists; a failure with no incumbent is not a solution.

## Extraction And Validation

After solving:

- check binary variables are close to 0/1 before rounding;
- convert internal units/conventions to report units;
- recompute summaries from arrays;
- recompute objective from input data and extracted decisions;
- run validation that reads only input data plus extracted/report arrays;
- write `"pass"` self-checks only after validation passes.

## Debugging Infeasibility

First suspect the model encoding. Common causes:

- mixing total output with output above minimum;
- applying startup/shutdown limits to the wrong quantity;
- using the wrong `t` or `t-1` index;
- over-constraining initial minimum up/down obligations;
- enforcing post-horizon obligations when the prompt excludes them;
- treating cost curves as feasibility constraints;
- choosing bad Big-M values;
- requiring segment/tier variables when the trigger did not occur.

Debug in stages: check shapes, relax one family at a time, add diagnostic slack variables, print largest violations, and compare local validation with final requirements.

## Repair LPs And Heuristics

A fixed-commitment repair LP is useful only if it includes every feasibility family judged in the final report. Do not repair only balance and capacity while omitting transition-dependent ramp, reserve, startup, shutdown, or minimum-duration limits.

Always rerun independent validation after repair.

## Reporting Discipline

- Use deterministic ordering and plain numeric values.
- Do not include placeholder values.
- Keep feasibility separate from proof quality/MIP gap.
- Use `null`/empty gap when no reliable bound exists, if the schema allows it.
- Do not trust solver status or self-reported `"pass"` strings without validation.

