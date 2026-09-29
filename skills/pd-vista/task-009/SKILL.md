---
name: pd-vista-r2r-mpc-control
description: Route public test and simulation feedback to one MPC guidance module at a time.
---

# PD-VISTA router for run-to-run MPC control

Work only with the task files and inputs visible in the current workspace. Start by
reading `task.md`, `r2r_simulator.py`, and `system_config.json`, then plan the MPC
design before editing the required output.

Initially, the original guidance modules are not visible. After a public test,
simulation, or runtime signal, the experiment controller may disclose exactly one
relevant module under `modules/<module-id>/SKILL.md`. Read a disclosed module before
the next repair attempt and use its bundled resources when appropriate.

Do not assume undisclosed modules exist, do not use hidden verifier answers, and keep
the simulator interface and required output format unchanged.
