---
name: pd-vista-travel-planning
description: Route public travel-planning verifier feedback to one original skill module at a time.
---

# PD-VISTA router for travel planning

Solve the task using only the files currently disclosed in the workspace.

After a verifier failure, the experiment controller may disclose exactly one
relevant original module under `skills/<module-id>/SKILL.md`. Read that module
before the next repair attempt and use its bundled scripts when appropriate.

Do not assume undisclosed modules exist. Do not use air travel unless the task
explicitly requires it.
