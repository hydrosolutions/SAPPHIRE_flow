---
status: READY
created: 2026-09-21
plan: 307
title: A model artifact cannot be imported through its own deployment — give it a staged path, not a base64 parameter
scope: Add an `artifact_path` parameter to the existing `import-model-artifact` deployment, reading from a read-only staging mount that every deployment declares, with a traversal guard and a content checksum. Explicitly NOT a new importer (`services/model_import.py` is unchanged), NOT the artifact store, NOT retraining or model discovery, NOT the basin-package import route (`cli/import_basin_package.py`, which already reads a path), NOT removing the existing `artifact_base64` parameter.
depends_on: []
blocks: [262]
open_decisions: []
source: 2026-09-21 — a live import attempt on the mac-mini staging host at v0.1.927 was refused by the Prefect API. The refusal, its byte counts and the mount topology below were all measured that day; the owner then set the requirement that model onboarding must be replicable on Nepali servers, which is what selects this shape over the two alternatives. The provenance values in T4 come from the owner's model tree at `2025-01-BARHKH/models/global/cmal_small` (its `config.yaml`, `checkpoints/best.pt` and `logs/train.log`), which is NOT in this repository — a reviewer cannot verify them from the checkout, and Plan 262 names the same tree.
---

# Plan 307 — import a model artifact from a staged path

## Status

**READY — set by the owner on 2026-09-21**, after six independent review passes (see below).
Written after Plan 262 T4 was blocked in execution. Owner chose this shape (option (a)) over an in-process call and over raising the
Prefect server limit, on the ground that **model onboarding must be replicable on Nepali
servers**.

Plan number **307 granted by the owner, 2026-09-21**. *(An earlier revision claimed it was "the
first unreferenced number after 306"; that was false — 274–299 are unreferenced repo-wide.)*

**Review: six passes, APPROVED.** Codex rounds 1–4 returned findings (4+2, 2+2, 1+2, 1); round 5
returned `VERDICT: APPROVE` with no findings at any severity, confirming that keyword-only
parameters preserve required provenance, that registration refresh precedes the live import, and
that checksum validation uses the same buffer passed to the unchanged importer. An independent
Claude pass in round 1 contributed 4 majors and 5 minors. **No model approved its own output.**

## The failure, measured

Plan 262 T4 recorded the risk in advance: *"`import_model_artifact_flow` takes the artifact as a
base64 `str` parameter (`flows/import_model_artifact.py:116-129`); 1.8 MB encodes to ~2.42 MB
crossing the Prefect parameter boundary. 'No new import machinery' is true, but this is by far the
largest artifact to cross it."*

Attempted on the mini, 2026-09-21, against the live `import-model-artifact` deployment:

```
422 Unprocessable Entity  POST /api/deployments/{id}/create_flow_run
Flow run parameters must be less than 524,288 bytes when serialized
(got 2,420,511 bytes)
```

| | |
|---|---|
| `checkpoints/best.pt` | 1,814,653 bytes (sha256 `84f1a4ef…8a26`, verified identical on host and inside the container) |
| base64 encoding | 2,419,540 chars |
| serialized parameters | **2,420,511 bytes** |
| Prefect server limit | **524,288 bytes** — 4.6× smaller |

**Nothing was written.** The refusal happens at flow-run creation, before any flow executes:
`model_artifacts` stayed at 902 rows and `model_artifacts WHERE group_id IS NOT NULL` at 0. The
failure is clean, which is why this plan can be written calmly rather than as a recovery.

⛔ **This is not a `cmal_small` problem.** `cmal_pool_pt` is the larger artifact, and any future
model is likelier to be bigger than smaller. The ceiling is structural.

## Why a staged path, and not the other two options

The owner's requirement — *replicable on Nepali servers* — decides it:

| option | why it fails the requirement |
|---|---|
| Call the flow in-process on the host | Not a procedure. No flow run, no recorded parameters, no operator route; each repeat is an ad-hoc snippet typed by whoever is at the keyboard. Cannot be written into a runbook. |
| Raise the Prefect parameter limit | A per-server setting someone must remember to replicate on every deployment, pushing megabytes through the Prefect database on every import, and needing to be raised again for a larger artifact. |
| **A staged path (this plan)** | The file lands on a mounted directory and the importer reads it. Identical procedure in Zurich and Kathmandu, because it is the same compose topology. |

🔑 **The repo already established this idiom.** `cli/import_basin_package.py` takes
`--package-dir <dir>` and states in its own docstring that no Prefect flow wraps it. Reading a
large input from a mounted path is how this codebase already imports bulk data; this plan brings
model artifacts onto the same footing **while keeping the flow run**, which the CLI route would
lose.

⚠️ **That precedent has the identical staging gap, and citing it as "already works" overstates
it.** *Independent review 2026-09-21 (minor).*
`docs/operations/basin-static-importer-runbook.md:54-61` says only `--package-dir
/path/to/nepal-dhm-basins` and never explains how a host directory reaches the hardened,
read-only-rootfs container — because no operator bind exists for it either. The precedent
establishes the **shape**; it does not demonstrate a working route. ⇒ **The mount T1 builds is
what that runbook has been missing**, which is also why Plan 306 depends on it (see Watch items).

## The mount topology, measured 2026-09-21

Read from `docker-compose.yml`, the mac-mini overlay, and `docker inspect` of the running worker:

🔴 **Every row below is attributed to its source, because an earlier revision mixed base compose
with overlay and with `docker inspect`, and got two rows wrong.** *Independent review 2026-09-21
(major).*

| mount | kind | where it comes from | note |
|---|---|---|---|
| `/data/artifacts` | named volume `model_artifacts` | **base compose** — rw on `prefect-worker` (`:146`), ro on `api` (`:331`) | the artifact store's own persistence — **not** an operator drop point, and a named volume an operator cannot drop a file into |
| `/data/raw` on **`prefect-worker`** | **nothing in the base compose** | **overlay only** — `docker-compose.macmini.yml:40` binds `/Users/sapphire/camels-ch:/data/raw:ro` | the base deliberately has no host bind here; the overlay header records that Plan 060 removed `sapphire_data:/data/raw` from the base |
| `/data/raw` on **`prefect-worker-ingest`** | **tmpfs** (`docker-compose.yml:199`) | base compose | ⚠️ **not a bind at all** — the previous table presented `/data/raw` as a host bind without saying on which service |
| `/data/cache`, `/tmp` | tmpfs | base compose | scratch |
| container rootfs | `read_only: true` (`docker-compose.yml:139`) | base compose | hardened; `docker cp` into it is refused outright (observed 2026-09-21) |

Two consequences the implementation must respect:

- **`/data/artifacts` is the wrong place to stage.** It is the store's volume, not an inbox, and
  it is a named volume rather than a host bind — an operator cannot simply drop a file into it.
- **The rootfs is read-only.** The staging mount must be a real bind (or volume), not a directory
  the container creates. `config/paths.py::_ensure_subdir` already handles `EROFS` by skipping,
  which means a staging root that only exists as a `mkdir` would silently not exist.

`SAPPHIRE_DATA_DIR=/data` is set on the four app services that need it (`prefect-worker` `:110`, `prefect-worker-ingest` `:171`, `prefect-worker-backup` `:230`, `api` `:293`), and `config/paths.py` already resolves
`raw`, `artifacts` and `cache` beneath it — so a staging subdirectory fits the existing shape.

## Tasks

### T1 — a staging mount every deployment declares

**Outcome.** A read-only staging directory — **`/data/incoming`** (D1) — is available to the
worker, and its location is resolved from configuration rather than hardcoded.

🔴 **The base compose cannot portably name a host path, and this task must say what it declares.**
*Independent review 2026-09-21 (major): the previous Outcome promised the mount "exists in the
worker on any deployment that follows the compose files" and verified a file placed in "the host
directory" — but the base compose deliberately carries no host bind for `prefect-worker`
(`/data/raw` is supplied only by the overlay, and Plan 060 removed the base one), while this
task's Out forbids creating the directory from inside the read-only container.*

🔴 **DECIDED, and stated precisely because the previous wording contradicted itself** — it said the
base "declares the mount point" while its own correction said that mount is "intentionally absent"
from the base. *Round 2 (major).* What each file does:

| file | declares |
|---|---|
| `docker-compose.yml` (base) | **no volume entry.** Only the configuration that *names* the staging root — the env var / `config.toml` default resolving to `/data/incoming`. The base has no portable host path to bind, exactly as with `/data/raw` since Plan 060. |
| each deployment's overlay | **the bind itself** — `<host dir>:/data/incoming:ro`. The mini's goes in `docker-compose.macmini.yml`; a Nepali server supplies its own. |

That keeps "the procedure is the same everywhere" true: the procedure and the container path are
identical, only the host directory differs per deployment.

**In.** `docker-compose.yml` — **the configuration naming the staging root only, no volume entry**
(per the table above); `docker-compose.macmini.yml` — **the bind** `<host dir>:/data/incoming:ro`
on `prefect-worker`, which is where `import-model-artifact` lands (it declares no pool and so runs
on `default`, `cli/register_deployments.py:193-198`); `config/paths.py` — a resolver beside
`resolve_artifact_dir`; `docs/spec/config-reference.toml` if a config field is added; and
`docs/standards/cicd.md` § the deployment's required mounts, which must say that **a deployment
without this bind cannot import an artifact by path** — that is the portability contract, and it
is the sentence a Nepali deployment will be set up from.

**Out.** Making it writable. Staging into `/data/artifacts`. A volume entry in the base compose.
Any change to `prefect-worker-ingest`, `prefect-worker-backup`, `api`, `init`, `postgres`,
`prefect-server` or `caddy`. Creating the directory from inside the container.

**Verification.** ⚠️ **Every compose check in this plan composes BOTH files** —
`docker compose -f docker-compose.yml -f docker-compose.macmini.yml config` — because the base
alone intentionally lacks the bind, and checking the base would assert the absence of the thing
this task adds. *Round 2 (major): two verification commands said plain `docker compose config`
while the text required overlay composition.* That composed config shows the mount read-only on
`prefect-worker` and on no other service; a file placed in the host directory is visible at the expected path inside the
worker; a write from inside the container fails.

**Pre-change.** The mount does not exist — `docker inspect` of the running worker on 2026-09-21
showed **no staging mount**. 🔴 *Round 3 (minor): this bullet previously said the inspection "lists
the five mounts in the table above", a total T3b's own Round-2 correction had already withdrawn.
Fixing one site and leaving its twin is the third instance of that failure in this plan's review
history — sweep by VALUE, never by site.*

### T2 — `artifact_path`, with a traversal guard and a required checksum

**Outcome.** `import_model_artifact_flow` accepts an artifact either as `artifact_base64` (today's
route, still valid below the limit) **or** as `artifact_path` resolved inside `/data/incoming` —
exactly one, never both, never neither. A path import additionally requires
**`expected_artifact_sha256`** (D3) and refuses on mismatch before the bytes are used.

**In.** `flows/import_model_artifact.py` **and `tests/unit/flows/test_import_model_artifact_flow.py`**,
which exercises the flow's parameter set through `Flow.validate_parameters`.

⚠️ *Round 2 (minor): an earlier revision claimed that test "will break". Measured — it supplies the
existing required parameters and asserts only that `artifact_base64` survives validation and
decodes to the original bytes (`:50-54`). It asserts nothing about the complete parameter set, so a
compatible keyword-only addition need not break it.* The file is in scope because T2 **adds**
schema tests to it — path-only validation succeeds, and no provenance field can be omitted — not
because it is expected to fail.
⚠️ *Independent review 2026-09-21 (minor): the previous wording said this file "only" while the
verification below mandates new tests — an In that forbids what its own verification requires.*

🔴 **"Add a parameter" understates the change, and the plan must say so.** *Independent review
2026-09-21 (major).* `artifact_base64: str` is a **required positional parameter** at
`flows/import_model_artifact.py:119`, followed by four more required ones — `trained_at`,
`training_period_start`, `training_period_end`, `expected_config_hash` (`:120-123`). "Exactly one,
never both, never neither" forces `artifact_base64` to become **optional**, which in turn forces
either defaulting or reordering those four. That is a required→optional change to a **registered
deployment's parameter schema**, which is the kind of change D2 was written to avoid.

🔴 **DECIDED — the signature becomes keyword-only after `model_id`.** *Round 2 (major): the
previous wording said T2 "must state which it does" and then did not state it — an annotation
where a decision was needed. Worse, the option it leaned toward is not valid Python: four required
positional parameters follow `artifact_base64` (`:120-123`), so defaulting only that one is a
syntax error, and defaulting the four would silently make required provenance optional.*

```python
def import_model_artifact_flow(
    model_id: str,
    *,
    artifact_base64: str | None = None,
    artifact_path: str | None = None,
    expected_artifact_sha256: str | None = None,
    trained_at: str,
    training_period_start: str,
    training_period_end: str,
    expected_config_hash: str,
    ...
) -> str:
```

Keyword-only parameters stay **required** even when earlier ones carry defaults, so this is the one
shape that admits the new optional inputs **without weakening any provenance field**. It is safe
here because nothing calls this flow positionally — measured: the only occurrence of
`import_model_artifact_flow(` in `src/` and `tests/` is the definition itself, and both Prefect and
the tests pass parameters by name.

Two guards, in this order:

1. **Traversal** — the opened file must be **provably inside the staging root at the moment it is
   opened**, not merely at the moment its name was checked.

   🔴 **Resolve-then-open is not sufficient, and an earlier revision specified exactly that.**
   *Round 4 (major).* This plan states that the staging directory is writable by anyone with host
   shell access — so between resolving the name and opening it, a checked component can be replaced
   with a symlink pointing outside the root. The checksum guard below does not help: it prevents an
   unintended **import**, but the forbidden **read** has already happened, and a read is the thing
   the traversal guard exists to prevent. The static-symlink test named below does not exercise
   this race at all.

   ⇒ **Open relative to a pinned staging-directory descriptor** (open the root once, then resolve
   each component under it without following symlinks out — `dir_fd` plus `O_NOFOLLOW`, or an
   equivalent containment-preserving mechanism), so containment is enforced by the open itself
   rather than by a name check that precedes it.
2. **Content** — digest the bytes and compare to `expected_artifact_sha256`, refusing **before
   those bytes reach `import_external_artifact` and before anything is written**.

🔴 **The digest must be taken of the exact buffer that is passed to the importer — never by
re-reading the file.** *Independent review 2026-09-21 (major).* The staging directory is writable
by anyone with host shell access, so a read-hash-then-read-again sequence leaves a window in which
the verified bytes and the imported bytes are different files. Read once, hash that buffer, pass
that buffer on.

`services/model_import.py` is untouched: it still receives `artifact_bytes`, and every guarantee
it holds (the strict `expected_config_hash` gate, the audited writer, the all-or-nothing
transaction) is unchanged.

⚠️ **`expected_artifact_sha256` and `expected_config_hash` are different things and must never be
conflated** — one is the digest of the checkpoint bytes, the other of the model's `config.yaml`.
Both are required on a path import, and a reviewer should check that neither is ever defaulted
from the other.

**Out.** Removing `artifact_base64`. Requiring `expected_artifact_sha256` on the base64 route
(the bytes are already in the parameter — there is nothing between the caller and the flow for it
to protect against). Any change to `import_external_artifact`. Accepting a URL, an object-store
URI, or any path outside the staging root.

**Verification.** Unit tests covering:

| case | expected |
|---|---|
| both `artifact_path` and `artifact_base64` given | refused |
| neither given | refused |
| `artifact_path` given, `expected_artifact_sha256` **omitted** | **refused** |
| path outside the staging root | refused, naming the root, **with no file opened** |
| symlink whose target escapes the root | refused, **with no file opened** |
| **a checked path component is replaced with an escaping symlink between the check and the open** | refused; **the outside file is never opened** (*Round 4 (major): the static-symlink row above does not exercise this race*) |
| staged file whose digest does not match | refused, **nothing written** |
| valid staged file, matching digest | imported; the bytes reaching `import_external_artifact` are identical to the file on disk |

🔴 **The third row is load-bearing and was missing.** *Independent review 2026-09-21 (major): an
implementation that simply skips verification when the checksum is absent would have passed every
other case in this list.* A required parameter that is only tested when supplied is not required.

Plus `uv run pytest tests/unit` and `tests/integration` clean.

**Pre-change.** The §"The failure, measured" 422 is this task's pre-change evidence: the route
does not exist and the existing one is refused at 4.6× the limit. ⛔ **A test asserting that
`artifact_path` raises `TypeError` today is NOT red evidence** — a signature error is not proof of
the fault, and this repo has already been caught by that once (Plan 262 T1; see
`feedback_red_first_must_prove_the_fault`). The new tests are acceptance tests for the new route,
and this plan says so rather than dressing them up.

### T3 — the operator procedure, written down

**Outcome.** A runbook section an operator can follow on any host — including one in Nepal —
without reading this plan.

**In.** `docs/operations/` (the model-artifact onboarding procedure: stage the file, checksum it,
run the deployment with `artifact_path`, verify the resulting rows) and the deployment
requirement in `docs/standards/cicd.md`. It names the provenance fields the operator must have in
hand and where each comes from, because three of them are easy to get wrong — Plan 262 T4
documents all three.

**Out.** A general model-onboarding guide. Anything about training.

**Verification.** The procedure is followed verbatim in T4 and the steps are corrected where they
did not match reality — not afterwards, from memory.

### T3b — roll the change onto the host and refresh the registered parameter schema

🔴 *Added by independent review 2026-09-21 (major): no task owned this, and T4 consumes the live
deployment.*

**Outcome.** The staging host runs the new image with the new mount, and the registered
`import-model-artifact` deployment accepts a path-only request.

**In.** A deploy of the built images following `docs/standards/cicd.md` § Upgrade procedure with
both overlays, a recreation of `prefect-worker` so the new mount is attached (a mount change
requires recreation, not a restart), and a re-run of deployment registration.

⚠️ **The registered parameter schema comes from the flow signature.**
`cli/register_deployments.py:195-199` registers this deployment from `import_model_artifact_flow`
via `flow.deploy()`, and compose runs registration inside `init`. Until registration re-runs, the
live schema is the old one — which still requires `artifact_base64` — and **T4 would be refused
by a stale schema for a reason that looks nothing like the real one.**

**Out.** Any code change. Any migration. Running the import itself.

**Verification.** `docker inspect` shows the staging mount on `prefect-worker` and on no other
service; the registered deployment's parameter schema lists `artifact_path` and
`expected_artifact_sha256`; a deliberately malformed path-only request is refused by the **flow's**
guard (naming the staging root) rather than by schema validation — which distinguishes "registered
and reachable" from "registered but never invoked".

**Pre-change.** `docker inspect` of the running worker on 2026-09-21 showed **no staging mount**
(its mounts are the artifact/NWP/BAFU volumes, the two config binds, the worker DB-password secret
and the NWP tmpfs). ⚠️ *Round 2 (minor): an earlier revision asserted a total of "five mounts",
which the table above does not support — that table lists selected relevant mounts plus rootfs
state and an ingest-service row, not a worker inventory.* The absence of a staging root is the
claim; the total is not. The registered schema also has no `artifact_path`.

### T4 — import `cmal_small` through the new route

**Outcome.** Plan 262 T4 completes: one ACTIVE `model_artifacts` row for `cmal_small` scoped to
`swiss-cmal-small-pilot`, with the provenance already established.

**Depends on T3b** — the new route must be deployed and re-registered first.

**In.** The provenance values, all verified 2026-09-21 and recorded here **in full** so they are
not re-derived — **with one deliberate exception: `expected_config_hash` MUST be recomputed** from
`config.yaml` in the owner's tree at import time, and the digest recorded below is the value that
recomputation must produce. *(Confirming review 2026-09-21: the blanket "not re-derived" wording
still contradicted that requirement after the first fold — the same fix-one-site failure, twice in
one document.)* ⚠️ *Independent review 2026-09-21 (major): an earlier revision abbreviated both
digests with an ellipsis while forbidding re-derivation — an instruction that could not be
followed. `services/model_import.py` compares `declared_config_hash != expected_config_hash` for
exact equality, so a truncated value is not merely inconvenient, it is unusable.*

🔴 **The training bounds MUST be timezone-aware, and bare dates cannot be passed.** *Round 3
(major), verified by execution:* the flow applies `ensure_utc(datetime.fromisoformat(...))` and
`types/datetime.py` rejects a naive value —
`ValueError: Naive datetime not allowed: datetime.datetime(1985, 1, 1, 0, 0)`. An earlier revision
listed the bounds as `1985-01-01` and `2020-12-31` while forbidding re-derivation, so an operator
following it verbatim would have been forced either to fail or to invent an offset at execution
time. Both are now written in full, timezone-aware.

⚖️ **The date→timestamp convention, stated so it is not invented later: midnight UTC on the named
date, with `training_period_end` naming the LAST DAY INCLUDED and stamped at that day's start.**
This is a convention *this plan is choosing*, recorded visibly for review rather than settled — it
interacts with the end-period stamping convention (Plan 267) and with point-vs-interval (Plan 258).
⚠️ **Plan 262 carries the same bare dates and therefore the same defect**; correcting it is a
change to a READY plan and is flagged in the interaction section, not applied here.

⚖️ **Plan 262 is AUTHORITATIVE for these values; the table below is a working copy.**
*Independent review 2026-09-21 (minor): 262 already records the same provenance — the
byte-identical config hash, the 1,814,653 → 2,419,540 → 2,420,511 byte chain, the
`2026-08-31T11:41:55Z` completion time, and the 0.1.346-bundle-under-0.1.356-runtime note. They
agree today, and duplicating them creates two copies to keep in sync.* If the two ever disagree,
**262 wins and this table is the error.**

| field | value | source |
|---|---|---|
| `trained_at` | `2026-08-31T11:41:55+00:00` | `logs/train.log:632` (`aquacast.pipeline:168`, "Trained: best val_loss=-17.94623 @ epoch 5"), 13:41:55 on a Europe/Zurich machine in CEST — owner-confirmed |
| `training_period_start` | **`1985-01-01T00:00:00+00:00`** | see the date convention below |
| `training_period_end` | **`2020-12-31T00:00:00+00:00`** | the config's global split is a fallback; 18 regions override and two train through 2020 |
| `expected_config_hash` | **recompute from `config.yaml` in the owner's tree at import time**; expected to equal `94ebec0fe4e000cecfd33ee8d50def9b8428b8f2e2ab7dbfeb77e2d04e580e45` | 🔴 *Amendment review 2026-09-21 (major): listing the digest while forbidding re-derivation conflicted with Plan 262's instruction to compute it at import time from the owner's tree — the whole point being that the repo constant would compare the repo file against itself.* **262's procedure governs: compute, then compare to the value here. A mismatch stops the import.** Byte-identity with the vendored copy was verified 2026-09-21. |
| `source_commit` | null | the bundle records aquacast `0.1.346`; the runtime pin is `0.1.356` |
| artifact | `checkpoints/best.pt`, 1,814,653 bytes | |
| `expected_artifact_sha256` | `84f1a4ef5099b2a9b76783413d250e9016be4d0adea99e14277b61ae2f788a26` | measured on the source file and re-verified after transfer to the host, 2026-09-21 |
| `notes` | the local-time training cut, and that we do not serve it | see below |

The `notes` field records what Plan 262 could not: this artifact was trained on daily data cut on
**local time per basin** (Swiss basins fixed CET year-round, modeller-confirmed 2026-09-21), while
SAP3 serves UTC-day aggregates today and the declared Swiss target is 06:00Z (Plan 252 OD-15).
**It is deliberately not served on the cut it was trained on**, and the record should say so.

**Out.** Changing anything to make the import pass. Re-deriving any provenance value **except
`expected_config_hash`, whose recomputation from the owner's tree is REQUIRED** — see In.

**Verification.** `model_artifacts` holds one ACTIVE row for `cmal_small` against the pilot group;
the three timestamps are stored un-conflated; a deliberate second run with a wrong
`expected_config_hash` is refused **before any write**.

## Owner decisions

**All three are closed. None is open.**

**D1 — ✅ CLOSED, owner 2026-09-21: a DEDICATED read-only mount, `/data/incoming`.** Not a reuse
of `/data/raw`, which is bound to `/Users/sapphire/camels-ch` on this host — Swiss-dataset-specific,
so a Nepali server would have had to bind an unrelated directory under a name meaning something
else. The mount gets its own host directory per deployment.

**D2 — ✅ RESOLVED BY SCOPE, not by a decision: `artifact_base64` survives.** This plan's Out
already forbids removing it. It is valid below the limit, it is what the tests use, and removing
it would be a breaking change to a registered deployment for no benefit. The operator route
becomes the path; the parameter stays. ⚠️ Recorded here so a reviewer does not re-open it as an
unanswered question.

**D3 — ✅ CLOSED, owner 2026-09-21: YES, the flow requires `expected_artifact_sha256`.** The
staging directory is writable by anyone with host shell access and the import writes an immutable
provenance record, so the import binds to the exact file the operator intended rather than to
whatever is at that path at that moment. It is checked **before the bytes are used**, and it gives
the runbook a step that catches a truncated copy — the transfer in this session was verified that
way, by hand. T2 owns the parameter and its refusal test; T3's procedure must produce the digest
before the import, not after.

## Watch items

- 🔑 **Plan 306 needs the mount T1 builds, and neither plan said so.** *Independent review
  2026-09-21 (minor).* 306's end-to-end goal is importing a basin package on the staging host,
  which requires a package **directory** readable inside the read-only-rootfs container — exactly
  what T1 creates. This plan's scope excludes the basin-package *route*; it does not exclude
  serving that route's staging need. Both plans carried `depends_on: []`. ⚖️ **Whether this becomes
  a formal dependency or stays a noted overlap is the orchestrator's call**, recorded here rather
  than decided.
- **`/data/incoming` is host-writable by anyone with shell access on the host.** That is why D3
  requires the checksum, and why T2 hashes the buffer it passes on rather than re-reading.

## Interaction with Plan 262 — flagged, not assumed

Plan 262 is `READY` and its T4 **In** says the import runs *"through the existing
`import-model-artifact` deployment — no new import machinery"*. This plan adds a parameter to that
deployment. Whether 262's T4 is amended to name the new route, or 262 simply consumes it, is a
**material change to a READY plan and needs its own review** — it is not something this plan may
decide on 262's behalf. ⛔ Nobody but the orchestrator sets READY, and nobody sets it on another
plan by implication.

## Exit gates

```bash
uv run pytest tests/unit
uv run pytest tests/integration
uv run ruff check src tests && uv run ruff format --check src tests
uv run pyright src
```

- Every T2 refusal case in the verification table has a test, **including the omitted-checksum
  case and the swap-between-check-and-open race**, and each refusal lands at the right boundary:
  - parameter-validation refusals happen **before the file is opened**, and containment is
    enforced **by the open itself** so that no file reachable only by a PATH leading outside the
    staging root is ever opened — not even transiently, and not even when the checksum would
    later reject its contents.
    ⚖️ **Scoped by owner decision 2026-09-21, after a clean-room review found the unconditional
    wording false:** a **hardlink** inside the root to an outside file is indistinguishable from
    an ordinary file and IS read. It is not an import vector — that would require a SHA-256
    preimage against a digest the operator computed off-host — and the residual hash oracle is
    closed by no longer reporting the computed digest on mismatch. 🔑 **The trigger for revisiting
    this:** a deployment where the staging directory is writable by someone less privileged than
    the operator must put the staging root on its own filesystem first;
  - the checksum refusal happens **before the bytes reach `import_external_artifact` and before
    anything is written**.
  ⚠️ *Independent review 2026-09-21 (major): the previous gate demanded that EVERY refusal happen
  before the file is opened, which a checksum can never satisfy — digesting requires reading. The
  two boundaries are now stated separately because they genuinely differ.*
- `services/model_import.py` is unchanged — shown by diff, not asserted.
- The **overlay-composed** config (`-f docker-compose.yml -f docker-compose.macmini.yml`) shows
  the staging mount read-only on `prefect-worker` and absent from every other service. ⚠️ *Independent review 2026-09-21 (minor): an earlier revision said "the other four", but the compose defines seven services besides `prefect-worker` (postgres, prefect-server, prefect-worker-ingest, prefect-worker-backup, api, caddy, init). Name services, do not count them.*
- T3b was completed before T4 ran, and the registered schema was confirmed to carry the new
  parameters — not assumed from a successful deploy.
- T3's procedure was followed verbatim to produce T4's rows, and was corrected in place wherever
  it did not match what actually happened.
- The `cmal_small` row records the local-time training cut in `notes`.
- T4's table was **reconciled against Plan 262 before execution** — 262 is authoritative, so any
  disagreement is resolved in 262's favour first — and the reconciled, timezone-aware values are
  what was passed. 🔴 *Round 3 (minor): this gate previously required every value to come "from the
  table in T4, in full", which contradicted T4's own statement that 262 wins on a disagreement.*

```json
{
  "phases": [
    { "id": "P1", "tasks": ["T1", "T2"], "parallel": true,
      "note": "T1 is compose/config/docs; T2 is the flow and its tests. No shared file." },
    { "id": "P2", "tasks": ["T3"], "depends_on": ["P1"],
      "note": "the runbook describes what T1 and T2 actually produced" },
    { "id": "P3", "tasks": ["T3b"], "depends_on": ["P2"],
      "note": "deploy + recreate the worker for the new mount + re-register the schema" },
    { "id": "P4", "tasks": ["T4"], "depends_on": ["P3"],
      "note": "the import itself; also gated on the Plan 262 T4 amendment review below" }
  ],
  "external_gates": [
    { "gate": "plan-262-t4-amendment-review",
      "blocks": ["T4"],
      "why": "262 is READY and its T4 In says 'no new import machinery'; amending it is a material change needing its own review" }
  ]
}
```

⚠️ *Independent review 2026-09-21 (minor): `docs/workflow.md:32` requires a closing JSON
dependency graph and this plan had none. The 262 amendment is recorded as an external gate on T4
rather than as prose, because the prose version stated the requirement without making it block
anything.*
