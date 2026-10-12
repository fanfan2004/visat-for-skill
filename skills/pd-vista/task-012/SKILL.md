---
name: pd-vista-invoice-fraud-detection
description: Disclose the fuzzy-matching and document/spreadsheet toolkits only after public retrieval or output feedback.
---

# PD-VISTA router for invoice fraud detection

Read the task instructions and the local input files first, then analyze the
invoices against the vendor and purchase-order records and write the required JSON
report to the task output path.

The original guidance modules are initially hidden. After a public signal about
retrieval, extraction, schema, or runtime behavior, the controller may disclose one
module under `./modules/<module-id>/SKILL.md`:

- `fuzzy-match` — fuzzy string matching and data reconciliation for entity names
  with spelling variations, typos, or formatting differences.
- `pdf` — PDF text and table extraction toolkit with helper scripts.
- `xlsx` — spreadsheet reading and analysis requirements and helpers.

Read a disclosed module before the next attempt and use only the files currently
visible to the Agent. Do not invent records, inspect hidden verifier state, or treat
undisclosed modules as available. Preserve the required report schema, page
indexing, and reason ordering requirements.
