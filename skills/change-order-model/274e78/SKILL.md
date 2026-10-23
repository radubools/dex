---
name: change-order-model
description: Label each eDIL project by whether its construction budget took change orders — read from the AIA G702 contractor's-draw summary in every monthly progress-report workbook (initial contract, change orders to date, count, billed to date) — and fit the kept text and picture criteria to that label with a cross-validated L1 (lasso) model, reporting honest outer-CV AUC and bootstrap selection frequencies. Needs edil-corpus and criteria-loop.
---

# Change-order model

| Module | Does |
|---|---|
| `co_labels` | reads the `Contractor` sheet (AIA G702) of every monthly workbook, in report order; the latest gives `had_change_orders`, `co_amount`, `co_count`, `co_share`, `billed_share`; stored in `corpus.project_labels` with the whole series |
| `window` | tunes the acceptance band: ranks every judged text criterion by mean confidence, scores contiguous windows of the ranking with the lasso, and compares the best with the best under shuffled labels (search-corrected p); `criteria.window_searches` |
| `baseline` | metadata-only models (state, building category, contract size, lender; + exposure) against the criteria, and an increment test that shuffles only the criteria columns |
| `lasso` | one feature per kept criterion (share of the project's chunks, or pictures, it fires on); L1 logistic for the binary label or Lasso for `co_share`; outer-CV score, coefficients, bootstrap selection; stored in `criteria.model_fits` |

```bash
cd /Users/raduparadovschi/dex/assets/construction
../../.venv/bin/python -m utils.co_labels              # (re)label every ingested project
../../.venv/bin/python -m utils.co_labels --summary
../../.venv/bin/python -m utils.lasso                  # had_change_orders
../../.venv/bin/python -m utils.lasso --min-billed 0.5 # negatives only if half billed
../../.venv/bin/python -m utils.lasso --target co_share
../../.venv/bin/python -m utils.lasso --target material_change_orders --material 0.05
../../.venv/bin/python -m utils.lasso --status dropped --permutations 20   # noise checks
../../.venv/bin/python -m utils.window --target material_change_orders
../../.venv/bin/python -m utils.baseline --target material_change_orders
```

## The label, and its two traps

- **Censoring.** A project with no change orders after two reports is not a
  negative yet — it may simply be early. `billed_share` (amount requested to
  date / current contract) says how far along it was; `labels(min_billed=...)`
  and `--min-billed` keep a no-change-order project only past that share.
  Projects with change orders are positives however early.
- **Misread cells.** A change-order total larger than the initial contract is
  flagged (`flags`) and left out by default; check it in the workbook.

About a quarter of projects have monthly reports only as PDFs and get no label
from this source; their G702 pages could be read by OCR later.

## Reading a fit

With a few dozen projects and ~100 criteria the model is badly underdetermined.
Trust the **outer-CV score with its spread**, not the in-sample fit, and read
**selection frequency** before coefficient size: a criterion selected in most
bootstrap resamples is a finding; the rest is the penalty's tie-breaking.
