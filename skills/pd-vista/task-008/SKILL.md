---
name: pd-vista-adaptive-cruise-control
description: Route public test and simulation feedback to one cruise-control guidance module at a time.
---

# PD-VISTA router for adaptive cruise control

Work only with the task files and inputs visible in the current workspace. Start by
reading `task.md`, `vehicle_params.yaml`, and the sensor data, then plan the control
implementation before writing the required output.

Initially, the original guidance modules are not visible. After a public test,
simulation, or runtime signal, the experiment controller may disclose exactly one
relevant module under `modules/<module-id>/SKILL.md`. Read a disclosed module before
the next repair attempt and use its bundled resources when appropriate.

Do not assume undisclosed modules exist, do not use hidden verifier answers, and keep
the required output format and vehicle parameters unchanged.
