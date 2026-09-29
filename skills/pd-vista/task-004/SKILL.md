---
name: pd-vista-powerlifting-coef-calc
description: Route public spreadsheet and calculation feedback to one powerlifting module at a time.
---

# PD-VISTA router for powerlifting coefficient calculation

Read the workbook documentation and inspect the existing sheets before editing. Keep
the source sheet unchanged, preserve the requested column order, use formulas for the
derived columns, and save the completed workbook at the task's required path.

Initially, the original spreadsheet modules are hidden. After public feedback about
workbook structure, formulas, calculation, or output validity, the controller may
disclose exactly one relevant module under `modules/<module-id>/SKILL.md`. Read the
disclosed module before the next attempt.

Do not assume undisclosed modules exist, do not use hidden verifier answers, and keep
the requested three-digit computation precision and workbook semantics intact.
