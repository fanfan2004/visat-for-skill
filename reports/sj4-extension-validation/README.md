# Local validation — 2026-10-14

No real model calls or full benchmark solves were started on Windows.

The latest targeted run passed 29 tests in `test_sj4_extension.py`,
`test_sj4_pd_m.py` and `test_zcode_local_backend.py`. Checks include actual
cross-process locking, a one-variable guarded SciPy 1.13.1 MILP, isolated
LibreOffice profile path mapping, masked parameterized failures, identical
multi-turn stopping rules, task materialization, and the previous PD-M/backend
regressions. The actual Mac ZCode launch and LibreOffice recalculation await
the Mac preflight and experiment.

Both packaged source snapshots and the artifact hash manifests reconcile.
SkillJuror `behavior-unit-diff.mjs` reports `risk=low` for all six transforms
(flat, single-file, PD for each task). Both `pd-path-hygiene.mjs` checks are low
risk with zero unresolved commands, broken links, or stale local paths.
Missing semantic units in PD comparisons are only the merged section headings;
original source guidance and support implementations are retained.

The broader existing task-metadata test could not load the six old vendor tasks
because this Windows workspace has no `vendor/skillsbench/tasks` checkout.
Those old fixtures were not created or modified for this extension. The new
tasks instead use the versioned snapshots included in this change.

`ruff` is not installed in the local Python environment; no Ruff pass is claimed.
Python compilation and `git diff --check` were used in addition to the tests.
