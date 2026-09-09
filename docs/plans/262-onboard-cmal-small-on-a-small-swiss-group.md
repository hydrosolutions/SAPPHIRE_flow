---
status: DRAFT
created: 2026-09-09
revised: 2026-09-09
plan: 262
title: Onboard cmal_small on a two-station Swiss group — the first deep-learning model in the pipeline
scope: Make the externally-trained `cmal_small` artifact run inside the ordinary forecast cycle on the mac-mini staging host, against a deliberately small station group. Five rails, all missing today: the shim subclass + vendored config, the `config_hash` the import path requires and no aquacast model exposes, an aquacast-enabled image for the forecast worker alone, an operator route to create a station group, and the artifact import itself. Explicitly NOT: fleet-wide onboarding, the 2020-2026 observation hole, the reanalysis tail gap (Plan 261 owns it), `cmal_pool_pt` promotion, retraining, skill scoring, or any change to `run_group_forecast`'s all-or-nothing behaviour. The operational forcing series itself is Plan 261's subject and is now a PREREQUISITE, not an accepted shortfall.
depends_on: [261]
blocks: []
source: 2026-09-09 — read-only measurement of the repo, the owner's model tree (`2025-01-BARHKH/models/global/cmal_small`, dated 2026-08-31), the aquacast revision pinned in `pyproject.toml`, and the live mac-mini staging database at v0.1.889.
---

# Plan 262 — onboard `cmal_small` on a two-station Swiss group

## Status

**DRAFT — reviewed once (Claude + Codex, 2026-09-09); all findings folded.**

Owner decision, 2026-09-09: **start now with the small group.** Of the three ways to
handle the history gate — wait for the fleet to reach 30 days in early October, start
now with the stations that already qualify, or backfill 2020-2026 from a non-LINDAS
source — the owner chose to start now. This plan implements that choice and nothing
else.

Owner decision, 2026-09-09: the pilot declares **`ModelTier.SKILL` +
`AlertEligibility.NO_EVENT_INFORMATION`** — ranked with the real forecasting models, but
barred from raising alerts until it has been seen to work on Swiss rivers.

### Owner reframe, 2026-09-09 — what it changed

The owner read the draft and identified that it "gets hung up on the past data": it
treated the incomplete past forcing series as an external blocker to route around, rather
than as a tier of ordinary operational assembly this system has simply never built. That
is now Plan 261's subject, 261 is a **prerequisite** of this plan, and the section that
declared the blocker is replaced by the one below. The pilot's first live run is a real
forecast, not a predicted failure.

### Review round 1 — what it changed

Both passes were repository-grounded and complementary; every finding was verified
against the code before folding. The design survived — all four claims the plan rests on
are confirmed true at the *pinned* aquacast revision — but three tasks were wrong in
shape:

- **T4's provenance was materially false** (Codex blocker) and is rewritten below.
- **T2 would have put torch into all five application images**, not the forecast worker
  alone; it now builds a distinct image.
- **T3 assigned a model that did not yet exist**; group creation and assignment are now
  separate tasks either side of the import.
- **The `_pooled` watch item was simply wrong** — GROUP dispatch never combines, a point
  Plan 241 had already established and this plan re-derived incorrectly.

Two gaps are flagged rather than closed; see **Flagged gaps** below.

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

**The 30-day lookback is the entire reason this model is reachable and 210 is not.**
Nothing on the staging host has 210 gap-free days of recent discharge, and nothing will
before Nepal. Two stations have 29 today.

Both models are **GROUP-scoped**. `_scope` returns `ArtifactScope.STATION` only when the
config names exactly one gauge (`aquacast/operational/model.py:671-677` **at the pinned
revision `5460f898`**); `cmal_small` names 15,489 training basins and carries no
`gauge_ids` key. Plan 241's note calling `cmal_small` "the immediate STATION-scoped
relaxable candidate" (`241-adopt-declared-horizon-semantics.md:363`) is **wrong on that
point**, and the follow-on it hands to "whichever plan first onboards a STATION-scoped
relaxable model" is therefore *not* inherited here. This plan does not touch the
one-timestamp cadence question.

Its horizon **is** relaxable: at the pin the predicate is
`horizon_fixed_reason(cfg, cfg.model) is None`, and the requirement declares
`horizon_semantics=AT_MOST` with `min_future_steps=1`
(`aquacast/operational/requirement.py:214-231` at `5460f898`). Plan 241 landed the
propagation and is deployed on the mini in 0.1.889, so `resolve_required_steps` returns
`min(1, 10) = 1` and our 5-day ICON feed clears the coverage gate.

⚠️ Every aquacast citation in this plan is given **at the pinned revision**, not at a
local checkout's HEAD. The two differ by roughly 27 lines and the horizon predicate was
renamed between them; the first revision of this plan cited the wrong one.

## ⛔ Proportionality — BINDING on this plan and on its review

This plan builds the smallest set of rails that lets one externally-trained artifact run
in the existing cycle on two stations. It must stay that size.

- **In:** one vendored config, one shim subclass, one `config_hash` property, one
  aquacast-enabled worker image, one operator script for group creation, one assignment,
  one artifact import, and one observed cycle.
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

**There is no group, and group *creation* is unrouted.** `station_groups` = 0,
`group_model_assignments` = 0. Outside tests, nothing in `src/` or `scripts/` calls
`store_group` or `add_station_to_group`. Assignment is *not* in the same position:
`create_group_assignment` (`services/model_onboarding.py:1023`) has two production
callers (`flows/onboard_model.py:600`, `services/model_onboarding.py:1818`) behind the
registered `onboard-model` deployment. It is group creation that has no route.

**No aquacast model can be imported today.** `import_external_artifact` requires a
non-`None` `config_hash` off the model (`services/model_import.py:386-393`); the FI
adapter proxies `getattr(self._model, "config_hash", None)`
(`adapters/forecast_interface.py:494`). `AquacastShim` does not define it, and neither
does aquacast. Every import refuses before any write. This is **not** an FI gap:
`config_hash` is absent from `forecast_interface/interface/protocol.py` and is a SAP3
import-provenance concept, so the fix belongs on our side of the boundary.

**Two stations qualify on discharge depth, and both are eligible to run.** Longest run
of consecutive `qc_passed` daily discharge ending 2026-09-09:

| code | name | consecutive days | run | status / kind / basin |
|---|---|---|---|---|
| 2009 | Porte du Scex | **29** | 2026-08-12 → 2026-09-09 | `operational` / river / gauged / has basin |
| 2091 | Rheinfelden-Messstation | **29** | 2026-08-12 → 2026-09-09 | `operational` / river / gauged / has basin |

Status is recorded because the group loop keeps only `OPERATIONAL` members and skips the
group entirely if none remain (`flows/run_forecast_cycle.py:3371-3390`) — a
non-operational member would be a hard block, and T3a's Out forbids writing
`station_status` to fix one.

Every other station has 13 or fewer consecutive days (34 stations at 12, 101 at 8)
because live BAFU ingest starts 2026-07-03 and only broadened to 138 stations on
2026-09-02. Both qualifying stations cross 30 on **2026-09-10**. Discharge in this
database also has a hole from 2020 to 2026 — CAMELS-CH history ends 2020, LINDAS is
real-time only — which is Plan 260's subject, not this plan's.

**Their forcing and NWP are in place, with one gap.** Both carry basin-average daily
`precipitation` (`meteoswiss_rprelimd`) and `temperature` (`meteoswiss_tabsd`), 32
values in the last 35 days. Both have 121 hourly steps × 21 members of precipitation and
temperature from the 2026-09-09 00Z ICON cycle, out to 2026-09-14. All 148 basins carry
216 `caravan:` attributes, and `cmal_small`'s 78 declared statics resolve **78/78**
(`docs/reference/cmal-small-static-features.md`, aliases merged in #252).

**The two are a useful contrast, and the plan must not blur it.**
`caravan_camels_ch_2091` is **in** `cmal_small`'s training basin list
(`train_basins.txt:2850`); `caravan_camels_ch_2009` is in none of train/val/test. Any
skill statement about 2091 is in-sample.

## The forcing series is a prerequisite, not a blocker to route around

**Superseded 2026-09-09 by the owner.** The first revision of this plan called the short
past-forcing window "the one blocker this plan does not remove", treated it as an external
fact of the world, and planned a *predicted failure* as the pilot's first deliverable. The
owner rejected that framing:

> "what we typically do for operational forecasting is, we concatenate reanalysis data
> with stale forecasts and with operational forecasts to get a complete time series of
> past to future forcing for the model. It seems to me like this plan may not take this
> into account. it gets hung up on the past data."

That is correct, and it is the right call. The shortfall is not a property of the data —
it is a tier this system has never built. Measured 2026-09-09: **169 NWP cycles are
retained** (2026-07-03 → today, 6-hourly, 148 stations, 5-day horizon each), every day in
the gap is covered by **21-22 cycles**, and the freshest covering cycle for each was
issued **on that day** — lead time ≈ 0. The values are not merely available; they are the
best estimate of what happened that exists.

**Plan 261, rewritten the same day, owns that assembly and now `blocks: [262]`.** What
remains true and worth keeping from the original analysis is the failure *mechanism*,
because it is what makes the shortfall invisible: a missing tail produces **fewer rows,
not nulls**, so the `max_nan=0` gate passes untouched
(`adapters/forecast_interface.py:1029-1038`) and the shortfall reaches the model as a
SHAPE failure. Per CLAUDE.md, `max_nan` is a pre-`predict` NaN gate only and shape
shortfalls are the model's responsibility.

**Sequencing (owner, 2026-09-09).** T1, T2 and T3a have no code dependency on 261 and are
built in parallel with it. **T5 — the first observed cycle — runs after 261 is deployed**,
so the pilot's first live run is a real forecast rather than a documented failure. This
supersedes the earlier answer to run T5 ahead of the forcing work; nothing else is
delayed by it.

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
  `CmalPoolPT` declaring `model_tier = ModelTier.SKILL` and
  `alert_eligibility = AlertEligibility.NO_EVENT_INFORMATION` (owner decision above;
  `ModelTier` admits only `SKILL`/`FALLBACK`, and `discover_models` raises if either
  classification is absent — `services/model_registry.py:34`). Plus a `config_hash`
  property **on the base class**: the SHA-256 hex digest of the vendored config file's
  bytes. Basing it on the file the shim already binds is what keeps the digest and the
  bound config from drifting — the discipline Plan 157 D1 assumed when it made the
  config package data.
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
`horizon_semantics=AT_MOST` and `min_future_steps=1` on both future-known variables.

⚠️ **Only the digest test can run without the `aquacast` extra.** Constructing any shim
imports `aquacast.operational.config`/`.model` in `AquacastShim.__init__`, and CI's
required unit job syncs without the extra, so every declaration test skips there
(`tests/unit/models/test_aquacast_shim.py:1-28`). Put the digest assertion in the
extra-free module and accept that the declaration assertions are locally-verified only —
do not claim CI coverage for them.

**Pre-change.** RED, and it must fail for the missing provenance rather than a missing
symbol: call `import_external_artifact` with a constructed aquacast shim and assert
today's `ConfigurationError` naming `config_hash`. A test that fails with
`AttributeError: CmalSmall` proves only that the class is unwritten.

### T2 — an aquacast-enabled image for the forecast worker alone

**Outcome.** The `prefect-worker` service — which runs both the forecast cycle and the
`import-model-artifact` deployment, since that deployment declares no pool and so lands
on `default` (`cli/register_deployments.py:193-198`) — runs an image containing aquacast
and torch. Every other service's image is unchanged.

**In.** `docker-compose.yml`: `prefect-worker` gets its **own** `build:` block and its
**own** image tag rather than the shared `x-app-build` anchor, built with
`WITH_AQUACAST=1` and the `aquacast_token` build secret in the **`environment:`** form
that `recap_dg_client_token` already uses (`docker-compose.yml:488-491`) — never a
`file:` entry, which would break `docker compose build` and `config` wherever that file
is absent. `docs/standards/cicd.md` gains a row for the new image and build argument.

**Out.** The shared anchor and its four other consumers (`prefect-worker-ingest`,
`prefect-worker-backup`, `api`, `init` — `docker-compose.yml:81,149,208,263,365`), which
must keep building the default, torch-free image. Making the extra a default anywhere.
Any change to `mem_limit`. The Dockerfile, which already accepts both inputs
(`Dockerfile:39-45`, `required=false`).

The Dockerfile states the requirement this task must honour: "only the forecast-cycle
worker image installs it, because it pulls torch and the whole ML stack"
(`Dockerfile:30-33`). Putting the argument on the shared anchor would have violated
that, and all five services share one image tag today, so it would have shipped torch
everywhere.

**Verification.** `docker compose config` shows the four anchor consumers unchanged and
`prefect-worker` on its own tag; a default build still yields an image without `torch`;
the worker's build yields one where `import aquacast` and `import torch` both succeed.
Record the built image size delta and the build duration here — an ML stack on an arm64
host is the one place this plan could turn out disproportionate, and a number settles
it.

**Pre-change.** RED: with today's compose, export `WITH_AQUACAST=1` and build; the
resulting worker image still has no `aquacast` and no `torch`. That discriminates
between "the argument is unwired" and "the argument is wired but the build failed" —
the measured `find_spec("aquacast") is None` alone does not.

**Risks to record with the size number.** arm64 wheel availability for the pinned torch;
the `rich>=15` `override-dependencies` already in `pyproject.toml`.

### T3a — an operator route to create a station group

**Outcome.** One idempotent, dry-run-by-default operator script creates a named station
group from station **codes** and adds members — the capability that has no non-test
caller today. It does **not** assign a model; that is T3b, and it cannot run yet.

**In.** `scripts/create_station_group.py`, its entry in the Dockerfile's curated
operator-script list (`Dockerfile:144-150`, Plan 218), the test that **locks** that list
(`tests/unit/deploy/test_dockerfile_operator_scripts.py` — adding a script is a
deliberate edit in three places, not an automatic pickup), the two documents that carry
literal copies of the list (`docs/deployment/mac-mini-staging.md:746`,
`docs/touchpoint-maps.md:777`), unit tests, and one integration test against real
Postgres. It threads a real `WritePrincipal` and `AuditLogStore`.

**Out.** A generalised fleet-onboarding flow. Any station-status change — the script
must never write `station_status`, given how `operational` was already applied to 111
stations by direct DB write. Removing stations from a group. Model assignment.

**Verification.**
```bash
uv run pytest tests/unit/scripts/test_create_station_group.py
uv run pytest tests/integration/scripts/test_create_station_group.py
```
Then, on the mini, a dry run followed by a real run creating one group holding **2009**
and **2091**, verified by reading back `station_groups` and its members. Both
script-list documents match the Dockerfile.

**Pre-change.** N/A — new capability. The script does not exist, so any test written
against it fails on the missing module, which is not discriminating RED evidence. The
`fetch_groups_for_model` assertion in T3b is the real acceptance criterion and is
recorded there.

### T3b — assign the model to the group (after the import)

**Outcome.** `cmal_small` is assigned to the pilot group at a daily `time_step`, and
`discover_group_runs` yields the pair.

**In.** The assignment path only — `create_group_assignment`, invoked through the
operator script extended with an `--assign-model` step, or through the existing
`onboard-model` route if that proves the smaller change.

**Out.** Everything in T3a.

⛔ **This cannot run before T4.** `group_model_assignments.model_id` is a foreign key to
`models.id` (`db/metadata.py:1035`), model *discovery* does not create that row, and the
`models` row for a fresh external model is created by the import itself
(`services/model_import.py:228-287`). The measured database has no aquacast model row,
so an assignment attempted in phase 1 would violate the FK. This ordering is the whole
reason T3 is split.

**Verification.** An integration test asserting that the assigned model is returned by
`fetch_groups_for_model` — the exact lookup `discover_group_runs` performs
(`services/run_group_forecast.py:231`). Without that, the assignment could write rows
the cycle never sees. Then, on the mini, the same assertion against the real group.

**Pre-change.** RED: the `fetch_groups_for_model` assertion, run before the assignment
exists, fails because the lookup returns nothing — not because a symbol is missing.

### T4 — import the trained artifact

**Outcome.** `cmal_small` has an ACTIVE `model_artifacts` row against the pilot group,
with **honest** provenance, imported through the existing `import-model-artifact`
deployment — no new import machinery.

**In.** A runbook section in this plan and the provenance values themselves. The
artifact bytes are `checkpoints/best.pt` (1,814,653 bytes) — already exactly the
`ModelBundle` that `deserialize_artifact` expects (`aquacast/operational/artifact.py`,
`aquacast/serialization.py`), so no conversion step exists or is needed.

**The provenance, corrected.** The first revision of this plan proposed three values that
are false. `import_external_artifact` requires these to describe the *real* external
training (`services/model_import.py:13-30`), and they are written into an immutable
record:

| field | value | why |
|---|---|---|
| `training_period_start` / `_end` | **1985-01-01 → 2020-12-31** | `1985-01-01 → 2016-12-31` is only the config's *global* split. 18 regions carry overrides and two of them (`camelsh`, `caravan_camels_cz`) train through 2020-12-31. The artifact was trained on the pooled union, so the covering range is what describes it. |
| `trained_at` | **the training-completion time, 2026-08-31 13:41:55** (`logs/train.log:632`) | **Not** `BundleMeta.created_at`: the bundle is stamped whenever a checkpoint snapshot is built, and the best checkpoint is from **epoch 5**, so its stamp long predates completion. **Not** the file's 14:05 mtime either. Three different numbers; only one is the training completion. |
| `expected_config_hash` | computed **at import time from `config.yaml` in the owner's model tree** | Copying the constant T1 pins into the repo would compare the repo file against itself — a check that can never fail. The owner's tree is the only independent source; the bundle carries no config digest. |
| `source_commit` | **left null** unless the training checkout revision is recovered | The artifact records aquacast **`0.1.346`**; the pinned revision is **`0.1.356`**, four days later. The pin cannot be the training source. |

**Out.** Writing a new importer. Importing `cmal_pool_pt`. Importing against a station.

**Verification.** `model_artifacts` holds one ACTIVE row for `cmal_small` scoped to the
pilot group; `models` holds a `cmal_small` row with `artifact_scope = 'group'`; the
provenance row records the three distinct timestamps un-conflated. A deliberate second
run with a wrong `expected_config_hash` is refused **before any write** — the invariant
`services/model_import.py` is built to hold.

**Pre-change.** N/A — this task runs existing, tested code against real data. Its
evidence is the resulting rows.

**Risk to record.** `import_model_artifact_flow` takes the artifact as a base64 `str`
parameter (`flows/import_model_artifact.py:116-129`); 1.8 MB encodes to ~2.42 MB
crossing the Prefect parameter boundary. "No new import machinery" is true, but this is
by far the largest artifact to cross it.

### T5 — the first forecast, after Plan 261 is deployed

**Outcome.** The cycle reaches `run_group_forecast` for the pilot group with a complete
past forcing series, and the outcome is recorded here with its cause, whatever it is.

**In.** One cycle run on the mini **after Plan 261's T1 is deployed**, and a written
result: whether `discover_group_runs` yielded the group, whether the future-coverage gate
passed (it should — required steps resolves to 1), whether the past leg reached the issue
time, and what the model returned.

**Out.** Changing anything to make it pass. Adjusting the model's declaration. Running
before 261 is deployed — that was the earlier plan and is superseded.

**Verification.** Worker logs for the cycle plus the `forecasts` rows for stations 2009
and 2091, quoted into this plan.

**Expected result, stated in advance so a surprise is legible — and it is DATED.**

Measured 2026-09-09, both pilot stations, precipitation and temperature, every source: the
30-day window (2026-08-10 → 09-08) holds **28 of 30 days**. Two are missing —
**2026-08-18**, which is INTERIOR, and **2026-09-08**, the tail. Plan 261 fills the tail
and deliberately excludes interior holes (owner decision, 2026-09-09), so on today's data
the window would still be 29/30 and the model would refuse.

⭐ **The interior hole ages out on its own.** The window start passes 2026-08-18 on
**2026-09-18**, and there are **zero** holes from 2026-08-19 onward (measured). So with
261's tail fill deployed, the first complete 30-day window — and the first stored forecast
— is available from **2026-09-18**, with no additional work and no interior-fill decision.

Run T5 on or after that date and the expected result is a stored forecast for both
stations: the pilot's two members are the *only* two with enough discharge depth, so the
all-or-nothing group behaviour has nothing to trip over; the statics resolve 78/78; the
future window is 5 days against a required 1.

⚠️ **The model does NOT tolerate a short or holed window — verified against the pinned
aquacast 0.1.356, not assumed.** A window needs `lookback_days` daily steps before the
issue (`aquacast/operational/model.py:786`), a coverage shortfall returns
`fi.ModelFailure(cause=fi.FailureCause.INPUT_DATA)` (`model.py:548-551`), and an interior
hole additionally trips the cadence check, which requires delivered spacing to equal the
declared step and names the first disagreement (`aquacast/operational/datasource.py:538-553`).
Running before 2026-09-18 therefore produces a typed, well-attributed refusal — not a
forecast, and not a crash.

⚠️ **What a failure would look like, and where to read it.** A group failure does **not**
surface as `ModelFailure`: `_output_from_result` converts it to `ModelOutputError`
(`adapters/forecast_interface.py:394-398`) and `run_group_forecast` logs
**`run_group_forecast.predict_batch_failed`** with `forcing_gaps`, returning `{}`
(`services/run_group_forecast.py:504-515`). `short_forcing_window` and `predict_failed`
are station-path events (`models/nwp_regression.py:406`,
`services/run_station_forecast.py:509`) and will **not** appear. If it does fail, check
`_group_forcing_gap_details` (`services/run_group_forecast.py:382`) before assuming the
cause.

⚠️ **The untested combination is the runtime pairing, not the data.** We run a
**`0.1.346` bundle under a `0.1.356` runtime** (see Flagged gaps). `ModelLoader.model_from_bundle`
rebuilds from `bundle.config` under a strict `load_state_dict`, so a weight-shaping
mismatch fails loudly rather than silently — but T5 is the first thing to exercise it, and
a load failure here is a *provenance* finding, not a forcing one. Do not conflate them.

**Pre-change.** N/A — an observation task.

### T6 — record what the pilot proved, and what it did not

**Outcome.** This plan records, in one place, what the first `cmal_small` forecast
demonstrated: that the rails hold end to end, and — separately — that nothing about its
*quality* has been established.

**In.** A status line in this plan, and the two standing caveats restated where a reader
of the result will meet them: **2091 is in the training basin list**, so its numbers are
in-sample and are never skill; and the pilot declares
`AlertEligibility.NO_EVENT_INFORMATION`, so it raises nothing.

**Out.** Any skill computation, hindcast, or scoring. Promoting the model. Adding
stations.

**Verification.** Bounded inspection: this plan carries the statement, and Plan 261
carries the reciprocal reference naming plan 262.

**Pre-change.** N/A — documentation only.

## Flagged gaps — needed from the modeller, not derivable here

1. **The artifact's training source commit.** The bundle records aquacast package version
   `0.1.346`, not a commit hash, so the exact training revision cannot be recovered from
   the artifact. `source_commit` stays null until the modeller supplies it.
2. **The timezone of `trained_at`.** `logs/train.log` timestamps carry no offset. The
   value must be confirmed as UTC or local before it is written, since
   `import_external_artifact` takes a `UtcDatetime`.

Related and worth the modeller's eye: we would run a **`0.1.346` bundle under a
`0.1.356` runtime**. `ModelLoader.model_from_bundle` rebuilds from `bundle.config` and
enforces a strict `load_state_dict`, so a weight-shaping mismatch would fail loudly
rather than silently — but nothing has exercised that combination yet, and T5 is the
first thing that would.

## Watch items, not tasks

- **`_pooled` cannot change on these two stations — confirmed, not assumed.** GROUP
  dispatch never combines: `build_combined_forecasts` is called only from the Phase B
  per-station loop (`flows/run_forecast_cycle.py:2932`, `:3262`), both *before* the
  Phase B2 group loop opens at `:3341`, and it takes an in-memory
  `MultiModelForecastResult` rather than reading the store, so a group forecast cannot
  re-enter combination on a later cycle either. `docs/touchpoint-maps.md:381` states it,
  and Plan 241 dropped its own T5 on exactly this ground
  (`241-adopt-declared-horizon-semantics.md:339-345`). A GROUP-scoped `cmal_small` is
  not a pooled contributor. *The first revision of this plan claimed the opposite and
  was wrong.*
- **The group's all-or-nothing failure mode is unchanged and load-bearing.** Any member
  station with inadequate future coverage returns `{}` for the whole group
  (`services/run_group_forecast.py:470`). With two members this is a real risk, and it
  is the main argument for keeping the pilot group small rather than "helpfully" adding
  stations.
- **2091 is in the training set.** Do not quote its scores as out-of-sample skill.

## Owner decisions

**All three are resolved. None is open.**

1. ✅ **2026-09-09** — `ModelTier.SKILL` + `AlertEligibility.NO_EVENT_INFORMATION`.
2. ✅ **2026-09-09** — the pilot group is **`swiss-cmal-small-pilot`** under the **default
   tenant**.
3. ✅ **2026-09-09, superseding an earlier answer** — Plan 261 is a prerequisite. T1/T2/T3a
   build in parallel with it; **T5 runs after 261 is deployed**, so the first live run is a
   real forecast rather than a documented failure.

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
- The vendored config's SHA-256 equals the pinned constant, **and** that constant equals
  a digest computed independently from the owner's model tree.
- `uv run pyright` no worse than the recorded ratchet baseline.
- The four shared-anchor services still build the default, torch-free image.
- `docs/standards/cicd.md` documents the new image and build argument;
  `docs/reference/cmal-small-static-features.md` records the vendored config;
  `docs/deployment/mac-mini-staging.md` and `docs/touchpoint-maps.md` script lists match
  the Dockerfile.
- Plan 261's T1 is deployed on the mini before T5 runs.
- T5's observed outcome is written into this plan with its cause, including whether the
  past forcing leg reached the issue time.
- No `station_status` was written by anything in this plan.
- No provenance field was filled with a value this plan could not source; the two
  flagged gaps are either closed by the modeller or still recorded as open.

```json
{
  "phases": [
    {
      "id": "phase-1",
      "tasks": ["T1", "T2", "T3a"],
      "parallel": true
    },
    {
      "id": "phase-2",
      "tasks": ["T4"],
      "depends_on": ["phase-1"]
    },
    {
      "id": "phase-3",
      "tasks": ["T3b"],
      "depends_on": ["phase-2"]
    },
    {
      "id": "phase-4",
      "tasks": ["T5", "T6"],
      "depends_on": ["phase-3"]
    }
  ]
}
```
