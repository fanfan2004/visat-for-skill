---
name: pd-vista-sec-financial-report
description: Disclose the SEC 13F analysis and fuzzy-name-search procedures only after public retrieval or output feedback.
---

# PD-VISTA router for SEC financial report analysis

Read the task instructions, the local `2025-q2` and `2025-q3` filing collections,
and `question.txt` style prompts first. Explore the quarterly filing folders and
answer from the filing data available in the workspace, writing the required JSON
structure to the task output path.

The original guidance modules are initially hidden. After a public signal about
retrieval, extraction, schema, or runtime behavior, the controller may disclose one
module under `./modules/<module-id>/SKILL.md`:

- `13f-analyzer` — SEC 13F holding analysis scripts: per-fund quarterly summaries
  (holdings count, AUM) and cross-quarter holding changes.
- `fuzzy-name-search` — fuzzy search scripts over 13F coverpages and stock CUSIPs
  that tolerate inaccurate fund or stock names.

Read a disclosed module before the next attempt and use only the files currently
visible to the Agent. Do not invent records, inspect hidden verifier state, or treat
undisclosed modules as available. Preserve the required answer schema and public
token logging requirement.
