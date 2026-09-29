---
name: pd-vista-python-scala-translation
description: Route public compilation and test feedback to one Python-to-Scala guidance module at a time.
---

# PD-VISTA router for Python-to-Scala translation

Work only with the task files and inputs visible in the current workspace. Start by
reading `task.md` and the supplied Python source, then plan the translation before
editing the required Scala output.

Initially, the original translation modules are not visible. After a public
compilation, test, or runtime signal, the experiment controller may disclose exactly
one relevant module under `modules/<module-id>/SKILL.md`. Read a disclosed module
before the next repair attempt and use its bundled resources when appropriate.

Do not assume undisclosed modules exist, do not use hidden verifier answers, and keep
the required output path and Scala 2.13 compatibility unchanged.
