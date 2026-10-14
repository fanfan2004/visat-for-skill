# Fixed SkillsBench task snapshots

Source: the local `skillsbench-1.1/skillsbench/tasks` archive examined on 2026-10-14.
The archive has no Git metadata, so no upstream commit identifier is claimed.

Only the two selected task statements, inputs, source skill bundles and original
`verifier/test_outputs.py` files are included. Contents are copied byte-for-byte;
no Dockerfiles or unrelated task data are required. This makes Mac execution
independent of the version of an existing `vendor/skillsbench` checkout.

Task and skill authorship/frontmatter and the xlsx license are preserved.
SHA-256 identities are recorded in each corresponding
`experiments/artifacts/sj4/task-01{4,5}/construction-evidence.json`.

These verifier sources and their expected values belong to the controller.
They are not copied into an agent workspace or used to construct the skill.
The local harness retains the existing instruction-based workspace restriction;
it does not provide an operating-system sandbox.
