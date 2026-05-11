# v0 Launch Roadmap

**Purpose**: the ordered punch-list to get v0 running on the Mac Mini
and then cleanly extend it. Not a plan document (plans live in
`docs/plans/`); this is a tracker that references them.

**Last updated**: 2026-05-11

**Decisions locked**:
- **D1 = B**: 5-station scope for initial Mac Mini deploy; Plan 068
  (onboard-stations parallelisation) runs in parallel for the
  169-station scale-up milestone.
- **D2 = C (hybrid)**: `@pytest.mark.slow` marker; `pyproject.toml`
  defaults `pytest` to `-m "not slow"`; nightly CI runs the full suite
  (timeout raised to 3600s); `workflow_dispatch` on both scheduled
  workflows for manual pre-merge runs; ritual documented in
  `docs/standards/cicd.md` — `gh workflow run integration-nightly.yml
  --ref <branch>` before merging major changes.

---

## Where we are

- **Phase ladder complete** (`docs/v0-scope.md` §H): Phases 1a/1b, 2,
  3, 4, 5, 6, 7, 7b, 8, 9, 10, 11 all done.
- **Dress rehearsal A3 GREEN end-to-end** — 2026-04-21 surfaced step
  4 + step 8 issues; the 2026-04-23 re-run (v0.1.412) had step 8
  passing live MeteoSwiss STAC. Plan 046 §A3 is now ready for the
  Stream C glue work. Report: `docs/deployment/dress-rehearsal-2026-04-21.md`.
- **Plan 067** (MeteoSwiss STAC adapter investigation) — DONE +
  archived at `docs/plans/archive/067-...md` (2026-05-11, commit
  `0a4819e`).
- **Plan 046** (Mac Mini deploy) — IN_PROGRESS; Streams A (dress
  rehearsal) green; Stream C (Mac Mini glue) is the next big chunk.
- **Sprint 2 (pyright/CI hygiene)** completed today (2026-05-11):
  - **Plan 070** (pre-commit + gate parity) — **DONE**, all 4 phases
    implemented + committed (commits `0223e8e`/`804ac59`/`0677c0e`/
    `b94d393`, tags v0.1.422–v0.1.429). Pre-commit hooks live;
    `uv run check` available; gate-parity audit script in
    `tools/gate_parity_check.py`. A4 (pyright-ratchet pre-commit hook)
    deferred — triggers when Plan 069 Phase 1 lands.
  - **Plan 073** (concrete pyright violations cleanup) — **READY**
    after six review rounds (commit `5d601fc`, tag v0.1.433). 67
    in-scope sites; not yet implemented.
  - **Plan 069** (pyright backlog ratchet + drain) — **READY** after
    four review rounds (commit `a3ae753`, tag v0.1.434). T15b
    inserted for "drain to zero" before T16 flip; not yet implemented.
- **LINDAS auto-retry** — workflow at `.github/workflows/live-lindas-weekly-autoretry.yml`
  added 2026-05-11 (commits `5e149c6`/`acbc0c1`, tags v0.1.435/v0.1.436).
  5-min sleep × 12-retry cap = ~1 hour of monitoring covering ~6
  BAFU publish cycles. First real test next Monday 2026-05-18.
  See `docs/decisions/bafu-lindas-monday-window.md`.
- **bump-my-version uv lock hook** — added 2026-05-11 (commit
  `9f2c92e`, tag v0.1.431). Eliminates recurring uv.lock drift
  commits after every patch bump.
- **Weather-history (Plans 071/072)** — drafted, four review rounds,
  zero blockers. v0b scope; not a deploy blocker.
- **Integration-nightly CI** — green since 2026-04-25 (17 consecutive
  scheduled successes through 2026-05-11). Stays as a watch-item only
  because the slow-marker set is sparse (1 of 221 collected items
  marked `slow`) — revisit marker coverage when Plan 073 implements.

---

## Sprint 1 — Deploy v0 to Mac Mini (target: ~1–2 weeks)

### 1.1 Housekeeping — DONE (2026-04-23)

- [x] Commit Plans 071/072 drafts (v0.1.396).
- [x] Uncommitted `pyproject.toml` hunk resolved — was already folded
      into commit `515fc68` (pyright flows/ carve-out). No drift.
- [x] `tests/fixtures/reference/performance_baseline.json` → gitignored
      per-environment artifact; local copy deleted.
- [x] D2 hybrid implemented (v0.1.397): `slow` in default addopts
      exclusion, class-level 600s timeout removed from
      `TestE2ePipeline`, nightly `--timeout=3600`.
- [x] `workflow_dispatch:` triggers confirmed present on both
      scheduled workflows.
- [x] "Before major merges to main" ritual documented in
      `docs/standards/cicd.md`.
- [x] Live LINDAS weekly fired manually → **SUCCESS** (first-ever run).
- [x] Integration nightly fired manually → first run with 3600s
      timeout surfaced a separate pre-existing bug (second pytest step
      collects 0 tests because default addopts marker-exclusion was
      inherited). Patched with `--override-ini "addopts="` in a
      follow-up commit.
- [x] 17 pre-existing ruff errors cleaned up (v0.1.399):
      `scripts/` excluded from T201, `notebooks/` excluded entirely,
      two trivial tweaks in `check_readiness.py`.
- [x] `uv.lock` drift reconciled (v0.1.400).

### 1.2 Land Plan 067 (already done — discovered 2026-04-23)

- [x] Implement Plan 067 Phase 2 (T2a probe rewrite, T2b client-side
      filter, T3.d `_CYCLE_HOURS = (0,6,12,18)`, T4a pagination cap
      raise → 800, D2 config-derived `max_fallback_steps`). Landed
      at commit `1318451` on 2026-04-21; 53/53 unit tests green.
- [x] Plan 046 Rev 12 present; Plan 067 Appendix (STAC checklist for
      Plan 047) appended.
- [x] Verify with a fresh manual forecast-cycle invocation against
      live MeteoSwiss STAC — DONE 2026-04-23 (see §1.3).

### 1.3 Re-run Plan 046 A3 step 8 — DONE (2026-04-23)

- [x] Execute the direct-invoke forecast-cycle step per Plan 046
      §A3.8 on MacBook Pro — **PASS** at v0.1.412 after 10 commits
      fixing live-path bugs (probe pagination, libeccodes0, dask,
      open_mfdataset rewrite, real ICON-CH2-EPS fixtures).
      `stations_attempted=4 stations_succeeded=4`, wall-clock ~30 min,
      NWP fetch ~2.9 GB dominates.
- [x] Updated `docs/deployment/dress-rehearsal-2026-04-21.md` with
      the 2026-04-23 re-run section. Plan 046 §A3 is now GREEN
      end-to-end.

**Gate**: all 9 A3 steps green on the rehearsal machine. ✅ **MET**

### 1.4 Decision point — Plan 068 or 5-station scope? (LOCKED — B + defer 068)

**Locked 2026-04-23**: **Choice B** + defer Plan 068 to post-v0.

- [x] 5-station scope for initial Mac Mini deploy.
- [ ] Document the 5-station scope in Plan 046 Stream A close-out
      (A5 rehearsal report amendment).
- [ ] **Plan 068 DEFERRED** to post-v0 milestone. Rationale: Plan 068
      depends on Plans 038 (store write atomicity) + 040 (hindcast
      dedup constraint), both currently DRAFT. Shipping 068 without
      its dependencies risks silent DB corruption at 169-station
      scale. Landing 038 + 040 + 068 as a bundle is ~1 week of work
      that is not on the v0 critical path (5-station deploy doesn't
      stress parallelism). Revisit the dependency bundle after v0
      is live on the Mac Mini.

### 1.4b Mac Mini hardware prep (can happen today, ~1 hour)

Physical/GUI steps only the operator can do on the machine. Independent of
§1.3 and Stream C — knock these out while Stream C is being built so the
only remaining Mac-Mini-side work is `./scripts/bootstrap-mac-mini.sh`.

**System config**
- [x] Set hostname to `sapphire-staging.local` (System Settings → General →
      Sharing → Local Hostname).
- [x] Create user `sapphire` (System Settings → Users & Groups → Add User…,
      account type **Standard** — not Admin, unless needed for initial
      Homebrew/Docker install; can be demoted afterward).
- [x] Set a password for `sapphire` and store it in the team password
      manager (macOS refuses auto-login on blank-password accounts).
      Record the location/path in `docs/deployment/mac-mini-staging.md` when
      that runbook lands — not here, this file is public.
- [x] Confirm FileVault is **OFF** (System Settings → Privacy & Security →
      FileVault). FileVault blocks auto-login entirely; staging box is
      LAN-only on trusted network per Plan 046 D1, so FileVault trade-off
      is acceptable.
- [x] Enable automatic login for `sapphire` (System Settings → Users &
      Groups → Automatically log in as… → `sapphire` → enter the password
      set above).
- [x] Energy: "Prevent automatic sleeping when the display is off" ON;
      "Start up automatically after a power failure" ON.
- [x] Defer automatic macOS updates to overnight only (System Settings →
      General → Software Update → Advanced).

**Docker Desktop**
- [ ] Install Homebrew if absent (`/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`).
- [ ] `brew install --cask docker`; launch Docker Desktop once to accept
      the license.
- [ ] Docker Desktop → Settings → General: enable "Start Docker Desktop
      when you log in".

**Storage**
- [ ] Attach USB SSD for backups.
- [ ] Format APFS (or HFS+), volume label `sapphire-backup`; confirm it
      auto-mounts at `/Volumes/sapphire-backup`.
- [ ] (Optional) pre-stage CAMELS-CH at `/Users/sapphire/camels-ch/` for
      the host bind-mount.

**Repo**
- [ ] `git clone https://github.com/hydrosolutions/SAPPHIRE_flow.git
      /Users/sapphire/SAPPHIRE_flow` so the path exists when Stream C
      artifacts land (`git pull` from there later).

**Gate**: Mac Mini boots into the `sapphire` user, Docker Desktop starts
automatically, `ls /Volumes/sapphire-backup` succeeds, repo cloned.

### 1.5 Plan 046 Stream C — Mac Mini glue + one-command bootstrap (~1 week)

Split into two coordinated deliverables:

#### 1.5a — Building blocks

- [ ] `scripts/launchd/ch.hydrosolutions.sapphire.plist` — main-stack LaunchAgent.
- [ ] `scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist` — watchdog LaunchAgent.
- [ ] `scripts/launchd/start-sapphire.sh` — wait-for-Docker-Desktop + `compose up -d` wrapper.
- [ ] `scripts/launchd/watchdog.sh` — watchdog runner.
- [ ] `scripts/launchd/install-launchd.sh` — copies plists + `launchctl bootstrap`.
- [ ] `docker-compose.macmini.yml` — overlay (USB backup bind-mount, CAMELS-CH host bind-mount, caddy TLS).
- [ ] `src/sapphire_flow/ops/__init__.py` + `src/sapphire_flow/ops/watchdog.py` — watchdog logic (health probe, backup-staleness check, hysteresis, Slack-if-present-else-log-only).
- [ ] `/etc/newsyslog.d/sapphire-watchdog.conf` template (version-controlled under `scripts/launchd/`).
- [ ] `docs/deployment/mac-mini-staging.md` — runbook (primarily points at the bootstrap script; covers the few unavoidable manual steps + troubleshooting).

#### 1.5b — One-command bootstrap

- [ ] `scripts/bootstrap-mac-mini.sh` — detects missing prereqs (Homebrew, uv, Docker CLI), creates `secrets/db_password` if absent, detects optional `secrets/slack_webhook_url`, verifies USB backup disk at `/Volumes/sapphire-backup`, brings up `docker compose -f docker-compose.yml -f docker-compose.macmini.yml up -d`, calls `install-launchd.sh`, loops on health-check until `ok`, prints final report with any remaining manual steps (Docker Desktop install if absent; System Settings if auto-login/auto-update not configured).

**Operator flow on the Mac Mini** (assumes §1.4b prereqs done — hostname,
`sapphire` user, Docker Desktop, USB SSD, repo cloned):
```bash
cd /Users/sapphire/SAPPHIRE_flow
git pull
./scripts/bootstrap-mac-mini.sh
```

Script handles the rest; prints clear guidance if it can't continue.

**Decisions locked** (2026-04-23):
- Hostname: `sapphire-staging.local`. User: `sapphire`. Repo path: `/Users/sapphire/SAPPHIRE_flow`.
- Slack webhook: skipped at install; watchdog log-only until added.
- CAMELS-CH: host bind-mount of pre-staged `~/camels-ch/`.
- Cross-platform (Linux): out of scope for v0; v1/Nepal follow-up.

**Gate**: `./scripts/bootstrap-mac-mini.sh` on a fresh Mac Mini (with Docker Desktop installed + USB attached) brings up the stack, installs LaunchAgents, and reports "ready" with `/api/v1/health` returning `ok`.

### 1.6 Plan 046 Stream D — operational validation (~1 week)

- [ ] 7-day continuous run on the Mac Mini with full flow cadence.
- [ ] Daily health-check reports (watchdog output + Prefect run list).
- [ ] Write go/no-go report at `docs/deployment/v0-launch-report.md`.

**Gate**: ≥ 95% of expected runs succeed; no operator intervention for
7 days; report committed.

### 1.7 v0 live 🎉

- [ ] Tag `v0.1.0` (first non-internal release) — bump minor, not patch.
- [ ] Update CLAUDE.md + MEMORY.md to reflect v0 deployed status.
- [ ] Communicate to stakeholders (hydrosolutions team, Nepal DHM
      contacts).

---

## Sprint 2 — Type / CI hygiene after deploy (target: ~1 week)

Independent of Sprint 1; can parallelise if someone picks it up while
Sprint 1's Mac-Mini glue is being built. Merge order is
**070 → 073 → 069** (all three plans agree on this).

### 2.1 Plan 070 — pre-commit + `uv run check` — DONE (2026-05-11)

- [x] Phase 1 (A1+A2+A3): pre-commit config + hooks installed + CLAUDE.md
      §Pre-commit hooks section. Commit `0223e8e` / v0.1.422.
- [x] Phase 2 (B1+B2): `src/sapphire_flow/cli/check.py` with
      `main() -> int`; `[project.scripts] check = ...`; cicd.md
      "Local gate helper" section. Commit `804ac59` / v0.1.426.
- [x] Phase 3 (C1+C2+C3): cicd.md CI tier table extended (26 rows);
      workflow header comments record first-fire run IDs;
      `tools/gate_parity_check.py` (allowlist + drift report).
      Commit `0677c0e` / v0.1.427.
- [x] Phase 4 (D-Final-Pass): cicd.md "Gate lifecycle" consolidating
      section. Commit `b94d393` / v0.1.429.
- [x] Status flipped READY → DONE. Commit `381b44e` / v0.1.430.
- [ ] **A4 deferred** — pyright-ratchet pre-commit hook; triggers
      when Plan 069 Phase 1 lands.

### 2.2 Plan 073 — fix 65+ concrete pyright violations (2–4 days)

**Status**: READY as of 2026-05-11 (commit `5d601fc` / v0.1.433),
after six review rounds. Not yet implemented.

- [ ] Phase 1 (Tier 1 latent crashes — T1–T5, parallelisable).
- [ ] Phase 2 (Tier 2 type-safety gaps — T6, T6b, T7, T8, T9, T10,
      T10b, T11, T12, parallelisable).
- [ ] Phase 3 (Tier 3 cleanup + T13.5a/b Literal migration with
      config-boundary validation).
- [ ] Phase 4 (T14 — verify global baseline ≤ 609).

**Gate**: `uv run pyright src/` reports ≤ 609 concrete errors;
config/{forecast_qc,qc}_rules.py validate `rule_id` against the
literal set at the TOML boundary; no new rule classes introduced.

### 2.3 Plan 069 Phase 1 — ratchet + re-enable pyright in CI (~1 day)

**Status**: READY as of 2026-05-11 (commit `a3ae753` / v0.1.434),
after four review rounds. Not yet implemented. Gated on Plan 073
landing per the merge order.

- [ ] T1: Verify + document existing `pyrightconfig.json` in
      `docs/standards/pyright.md` (template provided in plan).
- [ ] T1b: Fix stale `pyright --strict` references across docs (the
      plan file itself + archived plans are excluded from the grep).
- [ ] T2: Capture baseline at `tools/pyright_baseline.json` via
      `tools/pyright_baseline.py`; record live count in commit
      message body.
- [ ] T3: Wire CI ratchet check in `ci.yml` lint job (2 new `run`
      steps to `/tmp/pyright.json` + `tools/pyright_ratchet.py`).

**Gate**: `lint` CI job runs pyright again; baseline matches live
output; any new PR adding errors fails CI. Activates Plan 070 A4
(deferred pre-commit hook).

### 2.4 Plan 069 Phase 2+ — drain under ratchet (ongoing)

File-by-file drain, each PR ratchets the baseline down. Plan 069
specifies T4–T6 (flows/ concrete violations, 166 errors), T7–T14
(top-7 non-flows files), T15 (tail sweep with Plan 073 exclusion
list), **T15b** (drain to zero before flip), T16 (zero-tolerance).
Can run alongside feature work.

---

## Sprint 3 — v0b extensions (target: ~2 weeks)

Weather-history + retrain. Depends on Sprint 1 complete (real
operational data flowing).

### 3.1 Flip Plans 071/072 to READY (0 days)

- [ ] Review both plans one more time (you've been through 4 rounds;
      this is a final read-through).
- [ ] Set `Status: READY` on both.

### 3.2 Implement Plan 071 — MeteoSwiss open-data adapter (~1 week)

- [ ] Phase 1 (registry + supersession filter + converters guard).
- [ ] Phase 2 (adapter + recording tool + unit tests).
- [ ] Phase 3 (flows + deployment registration).
- [ ] Phase 4 (integration + docs).

### 3.3 Implement Plan 072 — hybrid resolver (~3 days)

- [ ] PerSourceStoreReader + HybridForcingSource + factory.
- [ ] Wire `DeploymentConfig.reanalysis_source` flag.
- [ ] Integration test + docs.

### 3.4 Plan 066 — retrain strategy (scope separately)

Plan 066 consumes 071/072. Pick up after 071/072 land.

---

## Open decisions

| # | Decision | Status |
|---|---|---|
| D1 | Land Plan 068 (A) or 5-station scope (B)? | **Locked: B** — 5-station deploy; Plan 068 in parallel for 169-station scale-up milestone. |
| D2 | Test-tier gating? | **Locked: C (hybrid)** — marker + default exclude + nightly + `workflow_dispatch` + documented pre-merge ritual. |
| D3 | Plan 049 (Cloudflare public URL) — include in v0 launch, or LAN-only? | Default: LAN-only (per Plan 046 D1). |
| D4 | Who runs the Sprint 2 hygiene? | **Resolved 2026-05-11**: orchestrator (Opus) + Sonnet 4.6 subagents. Plan 070 DONE; Plans 073 + 069 READY for implementation. |

---

## Watch-items (risks that could derail the schedule)

- **Mac Mini hardware issues**. USB disk for bind-mount, network
  stability, power management. Mitigation: Plan 046 Stream D
  explicitly monitors these.
- **169-station scope creep**. If the operator insists on 169
  stations at launch (D1 = A), add ~1 week for Plan 068 + A4 rerun.
- **BAFU LINDAS Monday-morning publishing window**. 2 of 3 observed
  Mondays failed (2026-04-27 succeeded; 2026-05-04 + 2026-05-11
  failed at ~07 UTC, recovered by ~14 UTC). Auto-retry workflow
  (`live-lindas-weekly-autoretry.yml`, 2026-05-11) probes every
  5 min × 12 retries = ~1 hour of coverage after a scheduled
  failure. First real test: 2026-05-18 Monday. If the auto-retry
  doesn't catch the recovery, manually trigger via
  `gh workflow run live-lindas-weekly.yml --ref main` and escalate
  to BAFU support (`abfragezentrale@bafu.admin.ch`). See
  `docs/decisions/bafu-lindas-monday-window.md`.

**Resolved watch-items** (2026-05-11):
- ~~Plan 067 re-run surfaces new STAC issues~~ — Plan 067 archived;
  2026-04-23 re-run passed.
- ~~Nightly CI noise~~ — `integration-nightly.yml` green since
  2026-04-25, 17 consecutive scheduled successes through 2026-05-11.

---

## How to use this doc

- Check items off as you land them.
- When a sprint completes, write a one-line note in the sprint
  header ("Sprint 1 done 2026-05-XX; v0 live").
- When reality diverges (it will), edit this doc rather than
  creating a new one. Keep it to one page of actual reading.
- For anything beyond this doc's scope, spawn a new plan in
  `docs/plans/` and link to it here.
