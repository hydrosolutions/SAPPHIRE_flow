# v0 Launch Roadmap

**Purpose**: the ordered punch-list to get v0 running on the Mac Mini
and then cleanly extend it. Not a plan document (plans live in
`docs/plans/`); this is a tracker that references them.

**Last updated**: 2026-04-23

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
- **Dress rehearsal** 2026-04-21: 7 of 9 A3 steps green on MacBook Pro
  (report at `docs/deployment/dress-rehearsal-2026-04-21.md`).
  Blocker = step 8 forecast-cycle (MeteoSwiss STAC pagination / probe
  bugs).
- **Plan 067** (MeteoSwiss STAC fix) — READY, fixes designed, awaiting
  commit. This unblocks dress-rehearsal step 8.
- **Plan 046** (Mac Mini deploy) — IN_PROGRESS; Streams A/B/C/D still
  have significant work.
- **Pyright** — disabled in CI; 675 errors under `flows/` carve-out.
  Plans 069/070/073 will bring it back. Not a deploy blocker.
- **Weather-history (Plans 071/072)** — drafted, four review rounds,
  zero blockers. v0b scope; not a deploy blocker.
- **Integration-nightly CI** — failing with `Timeout >600s` (same root
  cause as sequential onboard-stations). Alarm-fatigue risk.

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
- [ ] Verify with a fresh manual forecast-cycle invocation against
      live MeteoSwiss STAC (deferred to step 1.3 below — same
      invocation).

### 1.3 Re-run Plan 046 A3 step 8 (half-day)

- [ ] Execute the direct-invoke forecast-cycle step per Plan 046
      §A3.8 on MacBook Pro (or whatever machine is rehearsing).
- [ ] Update `docs/deployment/dress-rehearsal-2026-04-21.md` with the
      new result, OR create a fresh report if results diverge materially.

**Gate**: all 9 A3 steps green on the rehearsal machine.

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

**Operator flow on the Mac Mini** (tomorrow morning):
```bash
# Physical: attach USB SSD. Install Docker Desktop GUI + accept license.
# Enable auto-login for 'sapphire' user.

cd ~ && git clone https://github.com/hydrosolutions/SAPPHIRE_flow.git
cd SAPPHIRE_flow
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

### 2.1 Plan 070 — pre-commit + `uv run check` (½ day)

- [ ] Land Plan 070 Phase A (pre-commit config + hooks).
- [ ] Land Phase B (`uv run check` helper).
- [ ] Land Phase C (gate-parity audit + first-fire of scheduled
      workflows).

### 2.2 Plan 073 — fix 64 concrete pyright violations (2–4 days)

- [ ] Tier 1 (11 latent crashes). These are real bugs; fix with tests.
- [ ] Tier 2 (40 domain-type gaps).
- [ ] Tier 3 (13 cleanup).

**Gate**: `uv run pyright src/` reports ~611 errors (all
`flows/`-scoped or Unknown-cluster).

### 2.3 Plan 069 Phase 1 — ratchet + re-enable pyright in CI (~1 day)

- [ ] Capture baseline at `tools/pyright_baseline.json`.
- [ ] Ratchet CI script.
- [ ] Re-enable `uv run pyright src/` in `.github/workflows/ci.yml`.

**Gate**: `lint` CI job runs pyright again; baseline is equal to
current; any new PR adding errors fails CI.

### 2.4 Plan 069 Phase 2+ — drain under ratchet (ongoing)

File-by-file drain, each PR ratchets the baseline down. Can run
alongside feature work. No hard deadline; treat as background hygiene.

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
| D4 | Who runs the Sprint 2 hygiene? | Open; default: same operator as Sprint 1 unless parallelised. |

---

## Watch-items (risks that could derail the schedule)

- **Plan 067 re-run surfaces new STAC issues**. If MeteoSwiss's
  API has changed shape since the dress rehearsal, 067's Phase 2 may
  need further iteration. Mitigation: Plan 067 Phase 1 investigation
  already happened; Phase 2 design is evidence-based. Low risk.
- **Mac Mini hardware issues**. USB disk for bind-mount, network
  stability, power management. Mitigation: Plan 046 Stream D
  explicitly monitors these.
- **169-station scope creep**. If the operator insists on 169
  stations at launch (D1 = A), add ~1 week for Plan 068 + A4 rerun.
- **Nightly CI noise**. Unaddressed `integration-nightly.yml`
  failures erode signal. Sprint 1 step 1.1 handles the immediate
  fix; Plan 070 / Plan 068 are the longer-term solutions.

---

## How to use this doc

- Check items off as you land them.
- When a sprint completes, write a one-line note in the sprint
  header ("Sprint 1 done 2026-05-XX; v0 live").
- When reality diverges (it will), edit this doc rather than
  creating a new one. Keep it to one page of actual reading.
- For anything beyond this doc's scope, spawn a new plan in
  `docs/plans/` and link to it here.
