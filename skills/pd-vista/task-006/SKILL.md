---
name: pd-vista-fix-build-agentops
description: Route public build, dependency, testing, and timeout feedback to one repair module at a time.
---

# PD-VISTA router for build repair

Work only in the supplied repository workspace. First inspect the build failure and
write the requested analysis file, then record proposed changes as standard diff files,
apply them, and run the available tests or build checks.

Initially, the original repair modules are hidden. After public feedback identifies a
CI diagnosis, Python test, package/dependency, compilation, or timeout issue, the
controller may disclose exactly one relevant module under
`modules/<module-id>/SKILL.md`. Read it before the next repair attempt and use only
the files visible in the current workspace.

Do not assume undisclosed modules exist, do not use hidden verifier answers, and keep
the required analysis, patch, and repository paths unchanged.
