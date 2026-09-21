---
status: DRAFT
created: 2026-09-21
plan: 308
title: The import's provenance timestamps are independently supplied, not "distinct values"
scope: Correct two docstrings that describe `trained_at`, `training_period_start`/`_end` and `imported_at` as "distinct values", when what is true of them is that none is ever synthesized from another. Explicitly NOT a behaviour change, NOT a new check, NOT any change to what `import_external_artifact` does or accepts.
depends_on: []
blocks: []
related: [307]
open_decisions: []
source: 2026-09-21 — raised by a confirming review of Plan 307, which corrected the same false claim in that plan's operator runbook. Deliberately not fixed there: Plan 307's Out forbids touching `services/model_import.py`, and every one of its review rounds verified that file is unchanged against `main`. Both sites below were measured on that date.
---

# Plan 308 — provenance timestamps are independent, not distinct

## ⛔ Proportionality — BINDING on this plan and on its review

**This is a two-line wording fix.** It has a plan because the claim it corrects is a *provenance*
claim in an audited import path, and because Plan 307 could not make it without breaking a verified
invariant. ⛔ **Do not grow it.** No new validation, no new test asserting anything about these
values, no refactor of the import service. A review that asks for more than the Tasks below has
misread the scope.

## The defect

Two docstrings say these three provenance values are **distinct**:

| site | wording |
|---|---|
| `src/sapphire_flow/services/model_import.py:327` | *"…are three deliberately distinct values — none is ever synthesized from another."* |
| `tests/unit/services/test_model_import.py:12` | *"…are three distinct values, never fabricated from one another."* |

They are **distinct fields**, independently supplied. Nothing makes their *values* unequal, and
two of them legitimately can be: **a training run that finishes on the last day of its own
training period has `trained_at` equal to `training_period_end`.** That is ordinary, and a reader
who takes the docstring at face value would treat it as a fault.

🔑 **The second half of each sentence is TRUE and is the invariant worth keeping** — none of these
values is ever synthesized, defaulted or derived from another. The correction must preserve that
and drop only the claim about distinctness.

## What is measured (2026-09-21)

- **Nothing enforces the claim.** No assertion in `tests/unit/services/test_model_import.py`
  compares these values for inequality (`grep` for `assert … != …` over the three fields returns
  nothing). The defect is wording only — which is why this is a docstring fix and not a bug fix.
- **The same false claim reached an operator procedure.** Plan 307's runbook instructed an operator
  to verify the four timestamps were "distinct", which would have sent them investigating a
  non-problem. That instance is already corrected in Plan 307; this plan closes the two upstream
  sites it came from.
- `services/model_import.py` has **no diff against `main`** as of Plan 307's branch — the
  boundary that kept this from being fixed there is real and verified, not notional.

## Tasks

### T1 — say what is actually true at both sites

**Outcome.** Both docstrings state that the values are **independently supplied and never
synthesized from one another**, and neither claims they differ.

**In.** `src/sapphire_flow/services/model_import.py` and
`tests/unit/services/test_model_import.py` — docstrings only.

**Out.** Any change to `import_external_artifact`'s behaviour, signature, validation or error
paths. Any new test. Any change to what the function accepts or writes. ⛔ Adding a check that the
values differ — that would encode the very error this plan removes.

**Verification.** `git diff` shows docstring lines only. `uv run pytest
tests/unit/services/test_model_import.py` passes unchanged — the count before and after is
identical, because no test's behaviour is touched. `uv run ruff check src tests` clean.

**Pre-change.** The two quoted strings are present at the sites named above.

## Exit gates

```bash
uv run pytest tests/unit/services/test_model_import.py
uv run ruff check src tests && uv run ruff format --check src tests
```

- The diff contains no executable line.
- The surviving wording states the no-synthesis invariant and makes no claim about inequality.
- A grep for "distinct values" over `src/` and `tests/` returns no hit describing these three
  fields.

```json
{
  "phases": [
    { "id": "P1", "tasks": ["T1"],
      "note": "two docstrings; no behaviour change, no new test" }
  ]
}
```

## Changelog

**2026-09-21 — created.** Split out of Plan 307 at the owner's instruction rather than folded into
it, because 307's Out forbids touching `services/model_import.py` and its reviews verified that
file unchanged. Fixing it there would have invalidated a verified invariant to correct a nit.
