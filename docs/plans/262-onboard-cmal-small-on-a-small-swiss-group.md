---
status: DRAFT
created: 2026-09-09
plan: 262
title: Onboard cmal_small on a two-station Swiss group — the first deep-learning model in the pipeline
scope: Make the externally-trained `cmal_small` artifact run inside the ordinary forecast cycle on the mac-mini staging host, against a deliberately small station group. Five rails, all missing today: the shim subclass + vendored config, the `config_hash` the import path requires and no aquacast model exposes, an image built with the `aquacast` extra, an operator route to create a station group and assign a group model, and the artifact import itself. Explicitly NOT: fleet-wide onboarding, the 2020-2026 observation hole, the reanalysis tail gap (Plan 261 owns it), `cmal_pool_pt` promotion, retraining, skill scoring, or any change to `run_group_forecast`'s all-or-nothing behaviour.
depends_on: []
blocks: []
source: 2026-09-09 — read-only measurement of the repo, the owner's model tree (`2025-01-BARHKH/models/global/cmal_small`, dated 2026-08-31), the pinned aquacast revision, and the live mac-mini staging database at v0.1.889.
---

# Plan 262 — onboard `cmal_small` on a two-station Swiss group

## Status

**DRAFT — not reviewed.**

Owner decision, 2026-09-09: **start now with the small group.** Of the three ways to
handle the history gate — wait for the fleet to reach 30 days in early October, start
now with the stations that already qualify, or backfill 2020-2026 from a non-LINDAS
source — the owner chose to start now. This plan implements that choice and nothing
else.

## Why `cmal_small` and not `cmal_pool_pt`

Read from `cmal_small/config.yaml` in the owner's model tree (2026-08-31), against the
vendored `cmal_pool_pt.yaml` already in this repo:

| | `cmal_small` | `cmal_pool_pt` |
|---|---|---|
| lookback | **30 days** | 210 days |
| trained horizon | 10 days | 15 days |
| declared statics | 78 | ~50 |
| forcing | precipitation + mean temperature | same |
| head / backbone | CMAL (3 components) / 1-layer LSTM, hidden 256 | CMAL / LSTM, hidden 512 |
| checkpoint | `checkpoints/best.pt`, **1.8 MB** | — |
| train split | 1985-01-01 → 2016-12-31 (val 1981-1984, test 2017-2020) | 1985-01-01 → 2011-12-31 |

**The 30-day lookback is the entire reason this model is reachable and 210 is not.**
Nothing on the staging host has 210 gap-free days of recent discharge, and nothing will
before Nepal. Two stations have 29 today.

Both models are **GROUP-scoped**. `aquacast/operational/model.py:698` returns
`ArtifactScope.STATION` only when the config names exactly one gauge; `cmal_small` names
15,489 training basins and carries no `gauge_ids` key. Plan 241's note calling
`cmal_small` "the immediate STATION-scoped relaxable candidate"
(`241-adopt-declared-horizon-semantics.md:363`) is **wrong on that point**, and the
follow-on it hands to "whichever plan first onboards a STATION-scoped relaxable model"
is therefore *not* inherited here. This plan does not touch the one-timestamp cadence
question.

Its horizon **is** relaxable: `horizon_is_relaxable` returns True for a composed,
non-standalone, daily-only config with a non-attention head, so the requirement declares
`horizon_semantics=AT_MOST` with `min_future_steps=1`
(`aquacast/operational/requirement.py:198-211`). Plan 241 landed the propagation and is
deployed on the mini in 0.1.889, so `resolve_required_steps` returns
`min(1, 10) = 1` and our 5-day ICON feed clears the coverage gate.

## ⛔ Proportionality — BINDING on this plan and on its review

This plan builds the smallest set of rails that lets one externally-trained artifact run
in the existing cycle on two stations. It must stay that size.

- **In:** one vendored config, one shim subclass, one `config_hash` property, one
  compose build argument, one operator script for group creation/assignment, one
  artifact import, and one observed cycle.
- **Out:** onboarding the other 146 stations; changing `run_group_forecast`'s
  all-or-nothing `return {}`; the 2020-2026 observation hole (Plan 260); the reanalysis
  tail gap (Plan 261); promoting `cmal_pool_pt`; any retraining, hindcast or skill run;
  alert-threshold work; publishing anything to the dashboard.
- A review finding that proposes generalising the group route into a fleet onboarding
  flow, or that re-litigates the group model's failure semantics, is **out of scope** —
  record it against a new plan.

## What is measured (read-only, live mini, 2026-09-09, v0.1.889)

**The image has no aquacast.** In the running `prefect-worker`:
`importlib.util.find_spec("aquacast")` → `False`, `torch` → `False`. The
`cmal_pool_pt` entry point is present in `entry_points(group="sapphire_flow.models")`
but never constructs, which is why `models` holds 9 rows (3 virtual, 6 station) and none
is an aquacast model. No `docker-compose*.yml` passes `WITH_AQUACAST` or the build
secret, so a normal deploy cannot produce that image. `secrets/aquacast_token` **is**
present on the host.

**There is no group.** `station_groups` = 0, `group_model_assignments` = 0. Outside
tests, nothing in `src/` or `scripts/` calls `store_group` or `add_station_to_group`;
`create_group_assignment` (`services/model_onboarding.py:1023`) exists but has no
operator caller.

**No aquacast model can be imported today.** `import_external_artifact` requires a
non-`None` `config_hash` off the model (`services/model_import.py:386-393`); the FI
adapter proxies `getattr(self._model, "config_hash", None)`
(`adapters/forecast_interface.py:494`). `AquacastShim` does not define it, and neither
does aquacast. Every import refuses before any write. This is **not** an FI gap:
`config_hash` is absent from `forecast_interface/interface/protocol.py` and is a SAP3
import-provenance concept, so the fix belongs on our side of the boundary.

**Two stations qualify on discharge depth.** Longest run of consecutive `qc_passed`
daily discharge ending 2026-09-09:

| code | name | consecutive days | run |
|---|---|---|---|
| 2009 | Porte du Scex | **29** | 2026-08-12 → 2026-09-09 |
| 2091 | Rheinfelden-Messstation | **29** | 2026-08-12 → 2026-09-09 |

Every other station has 13 or fewer (34 stations at 12, 101 at 8) because live BAFU
ingest starts 2026-07-03 and only broadened to 138 stations on 2026-09-02. Both
qualifying stations cross 30 on **2026-09-10**. Discharge in this database also has a
hole from 2020 to 2026 — CAMELS-CH history ends 2020, LINDAS is real-time only — which
is Plan 260's subject, not this plan's.

**Their forcing and NWP are in place, with one gap.** Both carry basin-average daily
`precipitation` (`meteoswiss_rprelimd`) and `temperature` (`meteoswiss_tabsd`), 32
values in the last 35 days. Both have 121 hourly steps × 21 members of precipitation and
temperature from the 2026-09-09 00Z ICON cycle, out to 2026-09-14. All 148 basins carry
216 `caravan:` attributes, and `cmal_small`'s 78 declared statics resolve **78/78**
(`docs/reference/cmal-small-static-features.md`, aliases merged in #252).

**The two are a useful contrast, and the plan must not blur it.** `caravan_camels_ch_2091`
is **in** `cmal_small`'s training basin list; `caravan_camels_ch_2009` is in none of
train/val/test. Any skill statement about 2091 is in-sample.

## 🔴 The one blocker this plan does not remove

Past forcing is **two days behind**: the latest `historical_forcing.valid_time` for both
stations is 2026-09-07 00:00Z. The past window is the aligned lookback ending at the
issue bucket (`services/operational_inputs.py:569-576`, Plan 239 T1), and a missing tail
produces **fewer rows, not nulls** — so the `max_nan=0` gate passes untouched
(`adapters/forecast_interface.py:1029-1037` counts nulls and NaNs in the frame it was
given) and the model receives ~28 of the 30 daily steps it declares. Per CLAUDE.md,
`max_nan` is a pre-`predict` NaN gate only; **shape shortfalls are the model's
responsibility**, so aquacast will return `ModelFailure`, exactly the live
`short_forcing_window` / `predict_failed` signature Plan 239 T1b is about.

**Plan 261 (forecast-fill the reanalysis tail) is what makes the first green forecast
possible.** It is DRAFT on main awaiting owner READY. This plan is deliberately
structured so T1-T5 do not wait for it: every rail can be built, deployed and verified
against a *typed, correctly-attributed failure*, and 261 flips the last step green
without changing anything built here. T6 states that explicitly rather than pretending
the cycle will store a forecast on day one.

## Tasks

### T1 — vendor the config, add the shim subclass, and expose `config_hash`

**Outcome.** `CmalSmall` is constructible with no arguments, is discovered by
`discover_models`, declares `ArtifactScope.GROUP`, `horizon_semantics=AT_MOST` with
`min_future_steps=1`, and both aquacast shims expose a non-`None` `config_hash`, so
`import_external_artifact` no longer refuses at the provenance check.

**In.**
- `src/sapphire_flow/models/aquacast/configs/cmal_small.yaml` — copied **byte-identical**
  from `2025-01-BARHKH/models/global/cmal_small/config.yaml`. Its SHA-256 is
  `94ebec0fe4e000cecfd33ee8d50def9b8428b8f2e2ab7dbfeb77e2d04e580e45`; a test pins that
  value, so a hand-edit or a re-export cannot pass silently.
- `src/sapphire_flow/models/aquacast/_shim.py` — a `CmalSmall(AquacastShim)` beside
  `CmalPoolPT` (`CONFIG_FILENAME`, `model_tier`, `alert_eligibility`), plus a
  `config_hash` property **on the base class**: the SHA-256 hex digest of the vendored
  config file's bytes. Basing it on the file the shim already binds is what keeps the
  digest and the bound config from drifting — the same discipline Plan 157 D1 assumed
  when it made the config package data.
- `src/sapphire_flow/models/aquacast/__init__.py` — export `CmalSmall`.
- `pyproject.toml` — a `cmal_small` entry point; patch version bump.
- `docs/reference/cmal-small-static-features.md` — record that the config is now
  vendored in-repo, so the 78-name denominator is reviewable without the owner's
  Dropbox; the resolution table itself is unchanged.

**Out.** Any change to `CmalPoolPT`'s declaration or config. Any change to
`_to_aquacast_inputs` / `_to_canonical_result` / `_units.py`. Bumping the aquacast pin
(`5460f898`) — this plan uses the revision Plan 241 landed.

⚠️ Adding `config_hash` to the base class also makes `cmal_pool_pt` importable for the
first time. That is a deliberate, additive consequence and needs no separate decision:
it opens a path, it promotes nothing.

**Verification.**
```bash
uv sync --extra aquacast
uv run pytest tests/unit/models/test_aquacast_shim.py tests/unit/models/test_aquacast_shim_translation.py
uv run pytest tests/unit/services/test_model_import.py
uv run ruff check src tests && uv run ruff format --check src tests
```
Plus a test asserting `CmalSmall().input_requirement` carries
`horizon_semantics=AT_MOST` and `min_future_steps=1` on both future-known variables, and
that the vendored config's digest equals the pinned constant. Note the real-package
tests skip without the extra (`pytest.importorskip`), so the digest and declaration
tests must live where they run **without** it wherever possible, per the Plan 181 fixer
finding recorded at the top of `test_aquacast_shim.py`.

**Pre-change.** RED, and it must fail for the missing provenance rather than a missing
symbol: call `import_external_artifact` with a constructed aquacast shim and assert
today's `ConfigurationError` naming `config_hash`. A test that fails with
`AttributeError: CmalSmall` proves only that the class is unwritten.

### T2 — let a deploy actually build the aquacast image

**Outcome.** `WITH_AQUACAST=1 docker compose build` produces an image in which
`import aquacast` and `import torch` succeed; the default build is byte-identical to
today's.

**In.** `docker-compose.yml` only — the `x-app-build` anchor gains
`args: {WITH_AQUACAST: "${WITH_AQUACAST:-0}"}` and the `aquacast_token` build secret,
with the matching top-level `secrets:` entry. The Dockerfile already accepts both
(`Dockerfile:39-45`, `required=false`), so it needs no change.

**Out.** `docker-compose.macmini.yml` and every other overlay. Making the extra the
default anywhere. Any change to `mem_limit`.

**Verification.** `docker compose config` shows the build arg defaulting to `0`; a
default `docker compose build` still yields an image without `torch`; a
`WITH_AQUACAST=1` build on the mini yields one with it. Record the built image size
delta and the build duration in this plan — an ML stack on an arm64 host is the one
place this plan could turn out disproportionate, and a number settles it.

**Pre-change.** N/A — configuration wiring. The measured `find_spec("aquacast") is None`
in the running worker is the evidence that it is missing.

**Risks to state in the task record.** arm64 wheel availability for the pinned torch;
image size; the `rich>=15` `override-dependencies` already in `pyproject.toml`; and
`docs/standards/cicd.md` must gain a row describing the new build argument.

### T3 — an operator route to create a station group and assign a group model

**Outcome.** One idempotent, dry-run-by-default operator script creates a named station
group from station **codes**, adds members, and assigns a group-scoped model — the
capability that has no non-test caller today.

**In.** `scripts/create_station_group.py`, its entry in the Dockerfile's curated
operator-script list (`Dockerfile:144-147`, Plan 218), unit tests, and one integration
test against real Postgres. It threads a real `WritePrincipal` and `AuditLogStore` and
uses `create_group_assignment` rather than reimplementing the assignment invariants.

**Out.** A generalised fleet-onboarding flow. Any station-status change — the script
must never write `station_status`, given how `operational` was already applied to 111
stations by direct DB write. Removing stations from a group.

**Verification.**
```bash
uv run pytest tests/unit/scripts/test_create_station_group.py
uv run pytest tests/integration/scripts/test_create_station_group.py
```
Then, on the mini, a dry run followed by a real run creating one group of **2009** and
**2091** and assigning `cmal_small` at a daily `time_step`; verified by reading back
`station_groups`, its members and `group_model_assignments`.

**Pre-change.** RED: an integration test asserting that a group created by the script is
returned by `fetch_groups_for_model` for the assigned model — the exact lookup
`discover_group_runs` performs (`services/run_group_forecast.py:231`). Without that the
script could write rows the cycle never sees.

### T4 — import the trained artifact

**Outcome.** `cmal_small` has an ACTIVE `model_artifacts` row against the pilot group,
with honest provenance, imported through the existing `import-model-artifact`
deployment — no new import machinery.

**In.** A runbook section in this plan and the provenance values themselves:
`expected_config_hash` = the digest pinned in T1; `training_period_start/end` =
**1985-01-01 → 2016-12-31** (the config's train split); `trained_at` = the checkpoint's
own completion time, read from the bundle's `BundleMeta.created_at` and cross-checked
against the file's 2026-08-31 timestamp; `source_repository`/`source_commit` = the
aquacast repository and the revision pinned in `pyproject.toml`. The artifact bytes are
`checkpoints/best.pt` (1.8 MB) — already exactly the `ModelBundle` that
`deserialize_artifact` expects (`aquacast/operational/artifact.py`,
`aquacast/serialization.py:70-77`), so no conversion step exists or is needed.

**Out.** Writing a new importer. Importing `cmal_pool_pt`. Importing against a station.

**Verification.** `model_artifacts` holds one ACTIVE row for `cmal_small` scoped to the
pilot group; `models` holds a `cmal_small` row with `artifact_scope = 'group'`; the
provenance row records the three distinct timestamps un-conflated. A deliberate second
run with a wrong `expected_config_hash` is refused **before any write** — the invariant
`services/model_import.py` is built to hold.

**Pre-change.** N/A — this task runs existing, tested code against real data. Its
evidence is the resulting rows.

### T5 — one observed forecast cycle

**Outcome.** The cycle reaches `run_group_forecast` for the pilot group and the outcome
is recorded here with its cause, whatever it is.

**In.** One cycle run on the mini and a written result: whether `discover_group_runs`
yielded the group, whether the future-coverage gate passed (it should: required steps
resolves to 1), whether `predict_batch` returned a `ModelFailure`, and — if so — that
`_group_forcing_gap_details` (`services/run_group_forecast.py:382`) attributes it to the
past-forcing tail rather than to something unexplained.

**Out.** Changing anything to make it pass. Adjusting the model's declaration. Touching
`_pooled`.

**Verification.** Worker logs for the cycle plus the `forecasts` rows for stations 2009
and 2091, quoted into this plan.

**Pre-change.** N/A — an observation task.

**Expected result, stated in advance so a surprise is legible:** a `ModelFailure` with
`FailureCause.INPUT_DATA` naming a short past window, because forcing ends two days
before the issue time. A *stored forecast* at this point would mean the tail gap
resolved itself and must be explained, not celebrated.

### T6 — the remaining gate (not implemented here)

**Outcome.** This plan records, in one place, that the first green `cmal_small` forecast
needs **Plan 261** (or an equivalent decision about the reanalysis tail), and that
nothing else measured on 2026-09-09 stands between the pilot group and a stored
forecast.

**In.** A status line in this plan and a cross-reference from Plan 261.

**Out.** Implementing any part of 261.

## Watch items, not tasks

- **`_pooled` may change on these two stations.** Pooling needs ≥2 contributors sharing
  `valid_time`s (`services/forecast_combination.py:104`, `_MIN_POOLED_CONTRIBUTORS`).
  A new daily model on midnight phase could give 2009/2091 a pooled product they do not
  have today. That would be a *change in a live product* arriving as a side effect, so
  T5 must report the pooled row count for those two stations before and after. It is not
  a reason to hold the plan.
- **The group's all-or-nothing failure mode is unchanged and load-bearing.** Any member
  station with inadequate future coverage returns `{}` for the whole group
  (`services/run_group_forecast.py:414-460`). With two members this is a real risk, and
  it is the main argument for keeping the pilot group small rather than "helpfully"
  adding stations.
- **2091 is in the training set.** Do not quote its scores as out-of-sample skill.

## Owner decisions still open

1. **`model_tier` and `alert_eligibility` for `cmal_small`.** `CmalPoolPT` declares
   `SKILL` / `SKILL_FORECAST`. The same pair makes the pilot's output alert-eligible on
   two live stations from its first successful cycle. The alternative is
   `NO_EVENT_INFORMATION` while it is under evaluation, promoted later by a one-line
   change. **Recommendation: `NO_EVENT_INFORMATION` for the pilot** — the deployment is
   explicitly a test, and nothing about it has been validated against Swiss discharge.
2. **The pilot group's name and tenant.** Suggested: `swiss-cmal-small-pilot` under the
   default tenant.
3. **Whether T5 runs before Plan 261 is READY.** Recommendation: yes — the failure it
   produces is informative and the rails are what take time.

## Exit gates

```bash
uv sync --extra aquacast
uv run pytest tests/unit
uv run pytest tests/integration
uv run ruff check src tests && uv run ruff format --check src tests
uv run pyright src
```

- Every task's own verification has run, and T2's image-size and build-duration numbers
  are recorded here.
- The vendored config's SHA-256 equals the pinned constant, and that constant equals the
  digest of the file in the owner's model tree.
- `uv run pyright` no worse than the recorded ratchet baseline.
- The default (no-`WITH_AQUACAST`) image build is unchanged.
- `docs/standards/cicd.md` documents the new build argument;
  `docs/reference/cmal-small-static-features.md` records the vendored config;
  `docs/touchpoint-maps.md` is checked for the model-onboarding and forecast-cycle rows.
- T5's observed outcome is written into this plan with its cause, including the `_pooled`
  before/after counts for stations 2009 and 2091.
- No `station_status` was written by anything in this plan.

```json
{
  "phases": [
    {
      "id": "phase-1",
      "tasks": ["T1", "T2", "T3"],
      "parallel": true
    },
    {
      "id": "phase-2",
      "tasks": ["T4"],
      "depends_on": ["phase-1"]
    },
    {
      "id": "phase-3",
      "tasks": ["T5", "T6"],
      "depends_on": ["phase-2"]
    }
  ]
}
```
