# Behavior Unit Diff

- Source: `E:\git-hub\vista-mac\visat-for-skill\experiments\artifacts\sj4\task-014\single-file`
- Candidate: `E:\git-hub\vista-mac\visat-for-skill\experiments\artifacts\sj4\task-014\pd`
- Risk: `low`
- Reasons: none

## Summary

| Metric | Value |
|---|---:|
| Source files | 1 |
| Candidate files | 4 |
| Source units | 216 |
| Candidate units | 221 |
| Missing units | 3 |
| Added units | 8 |
| Semantic source units | 215 |
| Semantic candidate units | 216 |
| Semantic missing units | 3 |
| Semantic added units | 4 |
| Structural missing units | 0 |
| Structural added units | 4 |
| Semantic missing ratio | 0.014 |
| Semantic added ratio | 0.019 |
| High-risk missing | 0 |
| High-risk added | 0 |

## Missing By Kind

| Kind | Count |
|---|---:|
| `heading` | 3 |

## Added By Kind

| Kind | Count |
|---|---:|
| `file-path` | 3 |
| `heading` | 1 |
| `important-line` | 3 |
| `skill-frontmatter` | 1 |

## High-Risk Missing Samples

None.

## High-Risk Added Samples

None.

## Semantic Missing Samples

1. `heading` at `SKILL.md:1`: # unit-commitment-data-modeling
2. `heading` at `SKILL.md:170`: # unit-commitment-operating-rules
3. `heading` at `SKILL.md:414`: # milp-solver-workflow

## Semantic Added Samples

1. `heading` at `SKILL.md:6`: # energy-unit-commitment guidance
2. `important-line` at `SKILL.md:10`: - **`references/unit-commitment-data-modeling.md`** — Open when you need to parse resource fields, array shapes, periods, and report conventions.
3. `important-line` at `SKILL.md:11`: - **`references/unit-commitment-operating-rules.md`** — Open when you need to formulate transitions, reserve, ramping, initial conditions, and operating costs.
4. `important-line` at `SKILL.md:12`: - **`references/milp-solver-workflow.md`** — Open when you need to build sparse MILP constraints, extract incumbents, and validate solver results.

## Structural Missing Samples

None.

## Structural Added Samples

1. `file-path` at `references/milp-solver-workflow.md:1`: references/milp-solver-workflow.md
2. `file-path` at `references/unit-commitment-data-modeling.md:1`: references/unit-commitment-data-modeling.md
3. `file-path` at `references/unit-commitment-operating-rules.md:1`: references/unit-commitment-operating-rules.md
4. `skill-frontmatter` at `SKILL.md:1`: name: energy-unit-commitment-guidance
