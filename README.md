# PD-VISTA SkillsBench research scaffold

This repository is the configuration and data-layout scaffold for a three-condition pilot:

1. No-Skill
2. Native Skill
3. PD-VISTA

The first-stage claim is deliberately limited to the end-to-end effect of PD-VISTA relative
to No-Skill and Native Skill. This pilot does not identify the separate causal contribution
of progressive disclosure or verifier feedback.

## Repository boundaries

The repository enforces three separate concerns:

| Concern | Canonical location | Must not contain |
| --- | --- | --- |
| Task definitions and metadata | tasks/ | Skill content, trial logs, generated results |
| Native and PD-VISTA Skills | skills/ | Task prompts, trial logs, reports |
| Runtime and research artifacts | data/ and reports/ | Source task definitions, source Skills |

Runtime materialization belongs under:

~~~text
data/materialized/<condition_id>/<task_id>/<trial_id>/
~~~

Raw trajectories belong in data/raw/, normalized records in data/normalized/, aggregate data
in data/derived/, and final tables, figures, and trajectory cases in reports/. Delivery copies
are written by the integration workflow to the external yan/outputs/ directory; this scaffold
does not write outside its project root.

The benchmark checkouts vendor/skillsbench and vendor/skill-juror are external, read-only
inputs. This scaffold intentionally neither creates nor changes either checkout.

## Directory layout

~~~text
vista-skillbench/
├─ tasks/
│  ├─ manifest.yaml
│  └─ pilot/task-001 ... task-006/
├─ skills/
│  ├─ native/<task_id>/source-skill/
│  └─ pd-vista/<task_id>/{references,scripts}/
├─ conditions/
│  ├─ noskill.yaml
│  ├─ native.yaml
│  └─ pd-vista.yaml
├─ src/vista/{feedback,disclosure,runtime,telemetry,adapters}/
├─ experiments/pilot/
├─ analysis/
├─ tests/
├─ data/{raw,normalized,materialized,derived}/
├─ reports/{tables,figures,trajectory_cases}/
└─ vendor/
~~~

Source, analysis, experiment, Skill, and report directory boundaries are reserved by this
scaffold. Their implementations and generated contents belong to separate integration tasks.

## Pilot matrix

The manifest defines six workflow archetypes:

- one code implementation task;
- one API or tool-use task;
- one output-format task;
- one numerical-constraint task;
- one multi-stage workflow task;
- one test-repair-loop task.

Each task runs in all three conditions with three trials, producing 6 × 3 × 3 = 54 trials.
The model, harness, task input, verifier, timeout, and resource limits must stay identical
across conditions. Only the Skill treatment and trial seed may vary as declared.

The six task records are resolved pilot mappings to real SkillsBench tasks. Native Skills remain
read-only references into vendor/skillsbench; the adapter materializes a copy per trial. The
PD-VISTA wrappers are content-equivalent routing projections whose native files are copied under
the trial's modules/ directory only after feedback routing and audited by digest. Any unresolved mapping, source drift, or
unsafe source fails preflight instead of silently running a synthetic task.

For a low-cost real BenchFlow smoke, see [experiments/benchflow/README.md](experiments/benchflow/README.md).
It runs the real `codex-acp` agent through WSL/Docker for the lighter `task-002` in No-Skill and Native
conditions. The declared three-condition smoke plan intentionally fail-fast blocks PD-VISTA:
BenchFlow's static `--skills-dir` cannot establish the required dynamic disclosure loop.

## Condition semantics

- noskill.yaml prohibits mounting either Native or PD-VISTA Skill content.
- native.yaml materializes a copy from the manifest native_skill_path while preserving the
  source as read-only and disables feedback-driven disclosure.
- pd-vista.yaml loads only the root SKILL.md initially, then permits module disclosure based
  solely on agent-observable feedback. Native module files are staged outside the agent-visible
  trial workspace and copied into modules/ only after routing. Hidden verifier answers are
  forbidden.

All materialized workspaces are condition-, task-, and trial-scoped. A run must never edit
skills/native/ or skills/pd-vista/ in place.

## Windows 11 setup with uv

Install uv for the current user if it is not already available, then run these commands in
PowerShell from the project root:

主线程已提供项目隔离的 uv 包目录时，优先使用：

~~~powershell
Set-Location "D:\\vista"
$env:PYTHONPATH = "D:\\vista\\.tools\\uv-package"
python -m uv sync --project "D:\\vista"
~~~

~~~powershell
uv venv
.\.venv\Scripts\Activate.ps1
uv sync
Copy-Item .env.example .env
~~~

Place credentials only in .env or in the process environment. Never put a real API key in
YAML, TOML, source code, command history, or committed files. The project .gitignore excludes
.env and all generated run/report directories.

The committed uv.lock should be generated once the implementation task has finalized runtime
dependencies:

~~~powershell
uv lock
~~~

## Pre-run checklist

1. Resolve every pending SkillsBench task mapping in tasks/pilot/*/task.yaml.
2. Populate Native Skills without changing their original content or layout.
3. Populate content-equivalent PD-VISTA Skills with a root SKILL.md and routing manifest.
4. Confirm both vendor checkouts are treated as read-only.
5. Validate all YAML and TOML files and confirm the matrix expands to exactly 54 trials.
6. Confirm API credentials are present only through environment variables.
