---
status: BLOCKED
created: 2026-09-01
plan: 227
title: Bump ForecastInterface and aquacast together, and honour the new horizon semantics
scope: One coordinated dependency bump (FI v0.1.19 -> v0.1.20, aquacast 1937794c -> 5cbfdbb7) plus whatever the adapter needs to honour AT_MOST horizon semantics. No change to the forecast cycle, no new model, no image work.
depends_on: []
blocks: []
source: 2026-09-01 local test run — re-pinning aquacast alone fails to resolve; FI is the real coupling, not rich
---

# Plan 227 — the aquacast bump is really an FI contract bump

## Status

**BLOCKED — awaiting a fresh owner decision. The previous block was on a dependency that cannot
deliver it, and that is my error, not the owner's.**

On 2026-09-01 this plan was marked "blocked on Plan 151 (Phase 3) landing a two-horizon carrier".
**Plan 151 will never deliver that, and has already landed anyway:**

- **151 explicitly DROPS `AT_MOST` handling.** Its **D34** chose option (a) — *"DROP the T7
  route-time `AT_MOST` guard"* — because *"the axis is unreachable twice over (FI pinned at v0.1.19
  has no `horizon_semantics`; and `discover_models()` hands the runner a `ForecastInterfaceAdapter`
  that does not expose `input_requirement` at all)"*. Per-feature `AT_MOST` floors are recorded as
  **not expressible and an accepted cost** (151 D10a).
- **151's own revisit trigger is this very bump**: *"an FI bump to >= v0.1.20 AND a
  per-track-eligible model declaring divergent per-variable `min_future_steps`."*
- **151 is essentially complete**: T1-T4 merged 2026-08-18 (PR #182), T5-T7 on 2026-08-19
  (PR #192), T8b on 2026-08-28 (PR #227); the design marks Phase 3 **LANDED**
  (`docs/design/forecast-cycle-redesign.md:305`).

**So the dependency runs the OTHER way.** 151 waits for the FI bump; this plan was set to wait for
151. Left as written it would sit dormant forever. **The owner chose option (b) on my incorrect
framing and must be asked again**, with the real choice being: land the bump (which satisfies half
of D34's revisit trigger, and leaves `AT_MOST` unhonoured exactly as 151 already accepts), or open
a separate plan for the carrier work 151 deliberately excluded and marked settled.

## ⛔ Proportionality is a binding constraint on this plan AND on its review

Two pinned revisions move together, and the adapter learns one new concept. **Do not** turn this
into a redesign of horizon handling — the forecast-cycle redesign
(`docs/design/forecast-cycle-redesign.md`) already owns that, and this plan must not pre-empt it.

**Rules binding every reviewer:**

1. **"No findings" is a complete and welcome review.**
2. **A finding must name a CONCRETE FAILURE** with `file:line`.
3. **Do not propose new apparatus** — no new abstraction layer, no horizon framework.
4. **Do not propose splitting the bump.** D1 explains why coordinated is the smallest sensible
   unit — note it is a *preference backed by the resolver*, not a literal impossibility.
5. **Adding length is a cost.**

## Why this is not a one-line pin change — measured, not assumed

Re-pinning aquacast alone **fails to resolve**. Reproduced locally 2026-09-01 in a throwaway
worktree:

```
× Failed to resolve dependencies for `aquacast` (v0.1.346)
╰─▶ Requirements contain conflicting URLs for package `forecastinterface`:
    - git+.../ForecastInterface@v0.1.19   <- ours
    - git+.../ForecastInterface@v0.1.20   <- required by aquacast v0.1.346
```

**The `rich` floor was NOT the blocker**, contrary to the standing worry recorded at
`pyproject.toml:89-97`. Verified at the current pin: `override-dependencies = ["rich>=14,<15"]`
resolves rich to **14.3.3**, aquacast imports fine, and none of `aquacast/__init__.py`,
`operational/horizon.py`, `operational/model.py` or `operational/requirement.py` imports rich on
`main`. That override stays and remains correct; it is not this plan's problem.

## What the contract actually changed

FI v0.1.19 → v0.1.20 is **2 commits, 9 files**: *"feat(input): declare horizon semantics on
future-known variables"*. `FutureKnownVariable` gains:

- **`HorizonSemantics`** — `EXACT` ("fewer future steps is an error") or `AT_MOST` ("fewer is
  acceptable and yields a correspondingly shorter forecast");
- **`min_future_steps: int | None`**, required for `AT_MOST` and forbidden for `EXACT`, enforced by
  a `model_validator` with `validate_assignment=True`.

**The default is `EXACT`, which is today's behaviour** — so the FI bump alone changes nothing.

## The actual risk — corrected by review; my first framing was wrong

**An earlier draft claimed our side "has never heard of" horizon semantics. That is false.** It
was based on grepping only the adapter and the shim. They are referenced across `src/` and `tests/` in several places,
including a whole service — `services/horizon_semantics.py` (written 2026-08-15) with its own
tests at `tests/unit/services/test_horizon_semantics.py`.

**The real defect is subtler and worse: the handling exists and is INERT.**
`_model_declared_floor()` (`services/horizon_semantics.py:74`) asks a model for its own horizon
declaration — but the runners pass the *wrapped adapter* (`services/run_station_forecast.py:255`),
and the adapter exposes no `input_requirement`, only a private `_model`. So the floor lookup finds
no declaration and the AT_MOST path never engages. Bumping the pins alone would leave it just as
inert, while aquacast starts declaring semantics we still cannot see.

**And honouring AT_MOST is blocked further upstream than the adapter.** The chain:

1. the adapter exposes each feature's declared **maximum** `future_steps`
   (`adapters/forecast_interface.py:525`);
2. projection carries that maximum unchanged (`services/track_projection.py:64`);
3. candidate resolution **rejects** any feed shorter than it, *before the adapter runs*
   (`services/track_resolution.py:146`).

So a 5-step feed against an `AT_MOST(max=15, min=1)` requirement is thrown out upstream. **No
adapter-only change can make T2's "shorter horizon succeeds" gate pass.**

The root cause is a carrier limitation on our side, not a contract gap: `ForcingRequired`
(`types/forcing_track.py:85`) carries **one** horizon value per feature, so it cannot distinguish
an *acceptance floor* from a *useful maximum*.

## Decisions

- **D1 — Bump both together. This is the smallest sensible unit, not a hard impossibility.**
  Ordinary resolution *does* reject the v0.1.19/v0.1.20 URL conflict exactly as reproduced. But
  uv overrides are absolute and this repo already uses them (`pyproject.toml:98`), so an explicit
  FI v0.1.20 override would permit an FI-first intermediate commit. **The reverse is not
  possible** — aquacast v0.1.346 imports `HorizonSemantics`, so it cannot run on FI v0.1.19.
  An earlier draft called this a resolver impossibility; that was overstated.

- **D2 — Never SILENTLY treat `AT_MOST` as `EXACT`. Either honour it, or record explicitly that
  it is declared-but-not-honoured, and why.** The FI rule CLAUDE.md enforces is against
  *quietly* patching around a contract; a documented, deliberate limitation is not that. Which
  of the two applies is settled by T3, and the exit gates below branch on that choice — an
  earlier draft demanded honouring unconditionally while T3 permitted deferring it, which no
  implementer could satisfy. Silent coercion
  is the FI-adherence violation CLAUDE.md forbids: *if our side cannot express what the contract
  needs, file an issue upstream — do not patch around it on the SAP3 side.* If honouring AT_MOST
  turns out to need more than the adapter can express, **stop and raise an FI issue** rather than
  widening this plan.

- **D3 — Test against the REAL package, not fakes.** Proven necessary today: with aquacast absent,
  `tests/unit/models/test_aquacast_shim*.py` report **21 passed / 1 skipped**; with it installed,
  **27 passed / 0 skipped**. The fake-based majority would pass through a contract change
  unchanged, so a green suite without the extra proves nothing about this bump.

- **D4 — This plan does NOT enable aquacast in the deployed image.** That works at the *current*
  pin and is separate (Plan 218's outstanding gate + the `WITH_AQUACAST=1` build). Keeping them
  apart means a failure here cannot block getting a working model onto the mini.

## ⚠️ Carry-forward for Plan 151 — do not lose this

**Correction (2026-09-01, third review): the service is NOT dead code, and an earlier draft of
this section was wrong to imply it.** `resolve_required_steps()` consults a static provider table,
`HORIZON_CEILING_FLOORS` (`services/horizon_semantics.py:139`), where `cmal_pool_pt` carries a
floor of **5** (`types/ids.py:69-71`), and runner tests prove that fallback changes acceptance at
the real seam (`tests/unit/services/test_run_station_forecast.py:1150`). **Retiring the service
would remove working short-frame support.**

What IS inert is only the *model-declaration branch*:
`_model_declared_floor()` reads `input_requirement` off the model, but the runners pass the wrapped
adapter (`services/run_station_forecast.py:255`, `:285`; `services/run_group_forecast.py:389`),
which exposes only a private `_model`. So the floor lookup always finds nothing and the whole
service is dead code. **That is a latent defect on its own merits**, not a consequence of the FI
version, and Phase 3 should either fix it or explicitly retire the service. If Phase 3 ships
without addressing it, this plan's T2 becomes orphaned work.

## Tasks

### T1 — bump both pins and make the lock resolve
*In:* `pyproject.toml` FI `v0.1.19` → `v0.1.20` and aquacast `1937794c` → `5cbfdbb7`; `uv lock`.
*Exit:* `uv lock` succeeds; `uv sync --extra aquacast` installs; `rich` still resolves to 14.x
(the override must not have been disturbed).

### T2 — make the EXISTING horizon-semantics service reachable
*In:* whatever lets `_model_declared_floor()` (`services/horizon_semantics.py:74`) actually see a
model's declaration through the wrapper — the adapter needs to expose `input_requirement` rather
than hiding it behind `_model` (`services/run_station_forecast.py:255`). Plus an explicit
assertion that the shim's `model_copy(update=...)` (`models/aquacast/_shim.py:163`) preserves the
new fields, which it already appears to do.
*Out:* the two-horizon carrier (T3) · the forecast cycle's horizon handling · the redesign's scope.
*Exit:* an AT_MOST declaration is visible to the service through the wrapper, proven by a test
that fails today; the existing `test_horizon_semantics.py` suite still passes.

### T3 — SUPERSEDED. Do not execute either option.
*The (a)/(b) choice below was put to the owner on a false premise (that Plan 151 would deliver
the carrier). It is void. No implementer may act on it; the plan is blocked pending a fresh
decision — see § Status.*

#### Original framing, kept as the record
**This plan cannot deliver "a shorter horizon is accepted" and must not pretend to.**
`ForcingRequired` (`types/forcing_track.py:85`) carries one horizon per feature, and resolution
rejects short feeds before the adapter runs (`services/track_resolution.py:146`). Splitting it
into floor-and-maximum touches projection, resolution and assembly — **squarely inside
`docs/design/forecast-cycle-redesign.md`, which this plan is forbidden to pre-empt.**

*Exit:* **the owner decides** whether to (a) land T1+T2 now, leaving AT_MOST declared-but-not-yet-
honoured with that limitation recorded, or (b) hold the whole bump until the redesign carries two
horizons. **No implementer may choose this.**

### T4 — prove it against the real package
*In:* run the `importorskip`-gated shim tests with the extra installed.
*Exit:* the previously-skipped tests run and pass (expect 27+/0 skipped, not 21/1); full
`uv run pytest` green; ruff and the pyright ratchet pass.

## Does this need an upstream FI change? **No.**

FI v0.1.20 fully expresses `EXACT`, `AT_MOST` and the validated minimum, and aquacast v0.1.346
uses that API correctly. The remaining gap is **SAP3's own carrier** (`types/forcing_track.py:85`),
which holds one horizon where two are needed. **Filing an FI issue would misattribute a local
limitation to the shared contract** — precisely the diagnosis error CLAUDE.md's FI rule is meant to
prevent in the other direction.

## Non-goals

Enabling aquacast in the image (D4) · the `rich` override (verified fine) · the forecast-cycle
horizon redesign · onboarding `cmal_small` or any model · touching the mini.

## Exit gates

- `uv lock` resolves with both pins bumped, and rich stays 14.x.
- **If T3 = (a):** the adapter exposes `input_requirement`, `_model_declared_floor()` sees the
  declaration through the wrapper, and the plan records in-repo that AT_MOST is declared but
  **not yet honoured**, naming `types/forcing_track.py:85` as the reason. EXACT is unchanged.
- **If T3 = (b):** AT_MOST is honoured end-to-end — a feed shorter than the declared maximum but
  at or above `min_future_steps` succeeds — and EXACT still fails on a short horizon.
- Either way: locked by tests proven RED first, and no silent coercion.
- The real-package shim tests run (0 skipped) and pass.
