---
name: pd-vista-financial-modeling-qa
description: Disclose the PDF and spreadsheet toolkits only after public retrieval or output feedback.
---

# PD-VISTA router for financial modeling QA

Read the task instructions, the local `data.xlsx` workbook, and `background.pdf`
first. Analyze the workbook data and answer the modeling question, writing the
required numeric answer to the task output path.

The original guidance modules are initially hidden. After a public signal about
retrieval, extraction, schema, or runtime behavior, the controller may disclose one
module under `./modules/<module-id>/SKILL.md`:

- `pdf` — PDF text and table extraction toolkit with helper scripts.
- `xlsx` — spreadsheet reading, analysis, and recalculation requirements and helpers.

Read a disclosed module before the next attempt and use only the files currently
visible to the Agent. Do not invent records, inspect hidden verifier state, or treat
undisclosed modules as available. Preserve the numeric-only answer format.
