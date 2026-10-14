---
name: spreadsheet-pdf-fuzzy-matching
description: "Comprehensive spreadsheet creation, editing, and analysis with support for formulas, formatting, data analysis, and visualization (.xlsx, .xlsm, .csv, .tsv, etc), including recalculating formulas; comprehensive PDF manipulation toolkit for extracting text and tables, creating new PDFs, merging/splitting documents, and handling forms, including filling in a PDF form or programmatically processing, generating, or analyzing PDF documents at scale; and a toolkit for fuzzy string matching and data reconciliation, useful for matching entity names (companies, people) across different datasets where spelling variations, typos, or formatting differences exist."
---
# Spreadsheet, PDF, and Fuzzy Matching Toolkit

This skill covers three independent capabilities. Detailed procedures live in `references/`; load the reference that matches the task before starting work.

- **Spreadsheets**: create, edit, and analyze `.xlsx`, `.xlsm`, `.csv`, `.tsv` files, with mandatory formula recalculation via LibreOffice.
- **PDF processing**: extract text and tables, create/merge/split PDFs, and fill forms (fillable and non-fillable).
- **Fuzzy matching**: compare strings and reconcile entity names across datasets with spelling variations or typos.

## Capability Map And Loading Triggers

- **Excel files** (`references/xlsx.md`): load it for creating, editing, or analyzing Excel files with pandas or openpyxl; financial-model color coding, number formatting, and formula construction rules; formula verification checklists; and recalculation with the `xlsx/recalc.py` helper.
- **PDF files** (`references/pdf.md`): load it for text and table extraction (pypdf, pdfplumber, pdftotext), PDF creation (reportlab), command-line operations (qpdf, pdftk, pdfimages, pdftoppm), OCR of scanned PDFs (pytesseract), watermarking, encryption, and advanced libraries (pypdfium2, pdf-lib, pdfjs-dist).
- **PDF form filling** (`references/pdf.md`, PDF Form Filling section): load it whenever a task involves filling in a PDF form, whether or not the PDF has fillable form fields; covers `check_fillable_fields`, `extract_form_field_info`, `convert_pdf_to_images`, `fill_fillable_fields`, bounding-box annotation, and validation-image workflows.
- **Fuzzy matching** (`references/fuzzy-match.md`): load it for string similarity, best-match lookup, Levenshtein-style comparison with difflib (SequenceMatcher) or rapidfuzz, normalization, and entity resolution across datasets.

## Critical Constraints

- Every Excel model must be delivered with ZERO formula errors (#REF!, #DIV/0!, #VALUE!, #N/A, #NAME?).
- Always use Excel formulas instead of calculating values in Python and hardcoding them; the spreadsheet must recalculate when source data changes.
- Recalculating formulas is mandatory whenever formulas are used. Run `python xlsx/recalc.py <excel_file> [timeout_seconds]` from the skill root, then fix any reported errors and recalculate again. LibreOffice is assumed to be installed; the script auto-configures the LibreOffice macro on first run.
- When updating files with established formatting, exactly match the existing template conventions; existing template conventions override these guidelines.
- For PDF forms, complete the steps in `references/pdf.md` in order. First check for fillable fields with `python pdf/scripts/check_fillable_fields.py <file.pdf>` (run from the skill root), then follow the Fillable fields or Non-fillable fields workflow, including all validation steps. Do not skip ahead to writing code.
- Always normalize strings before fuzzy matching (case, punctuation, whitespace, common abbreviations).

## Bundled Helper Scripts

Run helper commands from the skill root, for example `python xlsx/recalc.py output.xlsx`.

- `xlsx/recalc.py`: recalculates all formulas in an Excel file via LibreOffice and returns JSON with error locations and counts.
- `pdf/scripts/check_fillable_fields.py`: reports whether a PDF has fillable form fields.
- `pdf/scripts/extract_form_field_info.py`: writes fillable field metadata (field_id, page, rect, type) to a JSON file.
- `pdf/scripts/convert_pdf_to_images.py`: converts each page of a PDF to a PNG image.
- `pdf/scripts/fill_fillable_fields.py`: fills fillable form fields from a `field_values.json` file and validates field IDs and values.
- `pdf/scripts/create_validation_image.py`: draws red/blue bounding-box validation overlays for non-fillable form analysis.
- `pdf/scripts/check_bounding_boxes.py`: checks that label and entry bounding boxes do not intersect and are tall enough for the font size.
- `pdf/scripts/check_bounding_boxes_test.py`: documents the expected behavior of the bounding box checks.
- `pdf/scripts/fill_pdf_form_with_annotations.py`: fills a non-fillable PDF by adding text annotations from a `fields.json` file.
