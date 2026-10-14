---
name: 13f-analysis-and-search
description: Perform various data analysis on SEC 13-F and obtain some insights of fund activities such as number of holdings, AUM, and change of holdings between two quarters. Also provides search capability in 13F, such as fuzzy search a fund information using possibly inaccurate name, or fuzzy search a stock cusip info using its name.
---

# SEC 13F Analysis And Search

Two capabilities for working with SEC 13-F filing data:

- **Fund holding analysis**: summary statistics of a particular fund in one quarter (total number of holdings, AUM, total number of stock holdings), dynamic changes of holdings between two quarters (newly purchased and newly sold stocks ranked by notional value), and the top funds holding a particular stock by notional value.
- **Fuzzy name search**: a search engine that provides you the ability to search a fund or stock meta information with a name. You don't have to provide the accurate name since the tool has built-in fuzzy string search algorithms based on Levinsteins.

## Helper Scripts

The helper scripts live under `13f-analyzer/scripts/` and `fuzzy-name-search/scripts/` and depend on `pandas` and `rapidfuzz`. They read tab-separated 13-F data files (`INFOTABLE.tsv`, `COVERPAGE.tsv`) for a quarter from the data root configured at the top of each script (`data_root = "/root"`).

Quick examples (run from the skill root; full usage and sample output in the references):

```bash
python3 13f-analyzer/scripts/one_fund_analysis.py \
    --accession_number ... \
    --quarter 2025-q2
```

```bash
python 13f-analyzer/scripts/one_fund_analysis.py \
    --quarter 2025-q3 \
    --accession_number <accession number assigned in q3> \
    --baseline_quarter 2025-q2 \
    --baseline_accession_number <accession number assigned in q2>
```

```bash
python 13f-analyzer/scripts/holding_analysis.py \
    --cusip <stock cusip> \
    --quarter 2025-q3 \
    --topk 10
```

```bash
python3 fuzzy-name-search/scripts/search_fund.py --keywords "bridgewater" --quarter 2025-q2 --topk 10
```

```bash
python3 fuzzy-name-search/scripts/search_stock_cusip.py \
    --keywords palantir \
    --topk 10
```

## Reference Loading Triggers

- **`references/13f-analyzer.md`** — Open when analyzing SEC 13-F fund holdings: one-quarter fund summary (total number of holdings, AUM, stock holdings) with `one_fund_analysis.py`, quarter-over-quarter changes (top buys and sells ranked by notional value) with `--baseline_quarter` / `--baseline_accession_number`, or top fund managers holding a stock CUSIP with `holding_analysis.py`.
- **`references/fuzzy-name-search.md`** — Open when searching 13-F data by name: fuzzy search a fund by filing manager name with `search_fund.py`, exact search a fund by accession number, or fuzzy search a stock CUSIP by issuer name with `search_stock_cusip.py`. Includes sample outputs and result field meanings (ACCESSION_NUMBER, FILINGMANAGER_NAME, FORM13FFILENUMBER).
