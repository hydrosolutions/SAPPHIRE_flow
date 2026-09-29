---
status: DRAFT
created: 2026-09-29
plan: 512
title: One clean, audience-labelled set of runbooks — so DHM/CHWRR staff can find the job they have to do
scope: Put every operator runbook in one place with one index, one job per file, and a clearly labelled audience. Documentation only; no code.
risk: low
depends_on: []
blocks: []
related: [510, 338]
open_decisions: []
closed_decisions: [D1]   # owner, 2026-09-29: agreed — one clean, clearly identifiable set for the jobs DHM/CHWRR must do
source: 2026-09-29 — the owner asked for runbooks that are clean and clearly identifiable by which job DHM/CHWRR staff must do.
---

# Plan 512 — one clean runbook set

## Why

Runbooks are spread over three places, so a newcomer cannot tell which one to open (measured 2026-09-29):

| place | what is there |
|---|---|
| `docs/operations/` | 7 runbooks: Mac-mini deploy, model-artifact import, basin/static import, Nepal station onboarding, Nepal forcing, recap gateway and probe |
| `docs/runbooks/` | 1 runbook (CHWRR DHM history import); PR #338 adds a second (model fine-tuning) |
| `docs/handover/` | DHM-facing guides (IT operations, hydrologist operations) |
| `docs/standards/cicd.md` | a Mac-mini deploy runbook and an access-token runbook, embedded in a standard |

Nothing says who a runbook is for, and two directories hold the same kind of document.

## Design

- **One home: `docs/operations/`.** `docs/runbooks/` is folded into it.
- **One index: `docs/operations/README.md`**, a table with one row per job: the job in plain words, the
  audience (**hydrosolutions operator**, **DHM/CHWRR operator**, or **modeller**), what it needs first,
  and whether it changes data.
- **One job per file, named by the job** (`import-chwrr-delivery.md`, not `chwrr-dhm-history-import.md`).
- **Every runbook opens with the same block:** audience, preconditions, what it changes, how to verify,
  when to stop.
- **DHM-facing jobs are marked and kept short.** The handover guides keep their role as orientation and
  link to the runbooks instead of repeating steps.
- **Embedded runbooks in standards** (`cicd.md`) stay where the rules live, but each gets a pointer row
  in the index so nothing is invisible.

## Tasks

- **T1** — write `docs/operations/README.md` (the index and the header template). Depends on PR #338 being
  merged, so the fine-tuning runbook is included and nothing collides.
- **T2** — move `docs/runbooks/*` into `docs/operations/` and rename by job. Sweep every reference first
  (plans, standards, scripts, tests that read docs from disk); an archived plan was once broken by a
  moved file.
- **T3** — add the audience header to the seven existing runbooks; link the handover guides to them.
- **T4** — (optional) a small test that every file in `docs/operations/` has the header block and an
  index row.

## Not in scope

Rewriting runbook content; a UI for triggering jobs; the database role the import needs (Plan 510).
