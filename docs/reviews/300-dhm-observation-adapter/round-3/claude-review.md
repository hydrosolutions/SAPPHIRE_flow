## Review — Plan 300, DHM water-level observation adapter (`ed2da78a`, worktree `/private/tmp/sapphire-plan300-review`)

Read-only Claude design/proportionality pass over the complete plan, `AGENTS.md`, `.claude/skills/review/SKILL.md`, `docs/workflow.md`, `docs/v0-scope.md`, the local captures, and the integration points the plan names.

I verified the plan's repository claims and they hold: the flow's eligibility filter (`flows/ingest_observations.py:601-609`), `_cursor_parameter_for_kind` returning `discharge` for every RIVER (`:114-127`, `:631-633`), `_run_qc_task`'s fixed `now-2h … now+1h` window (`:277-293`), `_load_adapter_endpoint`'s overlay-merged read (`:84-105`), `load_config` discarding `[adapters]` (`config/deployment.py:479-498`), `_run_bafu`'s unguarded LINDAS construction (`tools/record_fixtures.py:236-254`), the WEATHER skip-without-outcome convention (`adapters/hydro_scraper.py:123-136`), the failed-outcome-carries-no-observations invariant (`types/observation.py:93-97`), `[start, end)` fetch semantics (`store/observation_store.py:173-174`), exact-equality rule selection (`types/domain.py:160-167`, `services/qc.py:40-47, 252-253`), and the `LINDAS-only` wording in `docs/spec/types-and-protocols.md:250-256`. All four of T3's Pre-change regressions are genuinely discriminating against current code. In the captures, `day.json` holds 118 records with `count = 9223372036854775807` and a non-null `next` despite `limit=500`, `empty-page.json` carries a non-null `next`, and exactly one record sits on the `__gt` lower bound — so decisions 5 and 6 are grounded in the evidence, not inferred.

Findings below.

---

### 1. Medium — T3 rewrites the same QC window Plan 272 T2 owns, and neither the dependency paragraph nor the frontmatter records it

**Location:** `300:6` (`depends_on: []`), decision 5 at `300:133-144`, T3 In/Out at `300:344-346`, dependency paragraph at `300:422-425`.

Decision 5 and T3 change `_run_qc_task`'s read interval and its per-station call sites in `flows/ingest_observations.py`. Plan 272 T2 names `flows/ingest_observations.py` (the window) as one of three candidate fix sites (`272:138-139`), selected by its still-open decision D1 — whose option (b) is literally "Widen the QC window per parameter" (`272:98-100`). Plan 272 is an active DRAFT with `blocks: [264, 269]`.

Plan 300's dependency paragraph cites 272 only as the owner of *cadence reachability* and sequences it against 264's activation. It never records that 300 T3 edits 272 T2's candidate site. The violated requirement is `docs/workflow.md` § Plan Structure ("In / Out — bounded files and explicit exclusions") and the cross-plan sequencing discipline this plan applies rigorously everywhere else; the consequence is a silent conflict for whichever plan lands second, and a DHM-scoped precedent set inside a function whose fix shape 272 has deliberately not yet chosen.

**Smallest sufficient correction:** one sentence in § Standards and dependencies stating that T3's `_run_qc_task` interval change occupies Plan 272 T2's D1(b) candidate site, that 300's extension is DHM-scoped and does not pre-empt D1, and that whichever lands second rebases onto the other.

---

### 2. Medium — "flow-owned HTTP clients must close on success and failure" demands teardown that T3's In/Out does not bound, and the flow has none today

**Location:** `300:244-245`; T3 In/Out `300:344-346`; T3 verification `300:386-388` ("HTTP-client ownership").

`ingest_observations_flow` has no `try/finally` anywhere. It creates `httpx.Client(timeout=30.0)` at `flows/ingest_observations.py:550-558` and never closes it (the DB `_conn` at `:535-540` is likewise assigned and never referenced again). Satisfying "close on success and failure" therefore requires adding exception-safe teardown spanning at least from adapter construction (`:549`) to the fetch task (`:636`) — a structural edit to a live Swiss flow. T3's In/Out bounds the flow edit to "production setup, cursor helper/map and `since` loop, `_run_qc_task` interval parameters and its per-station call sites", none of which covers a `try/finally` around the body. As written the task either authorises an unbounded refactor or leaves a stated requirement (and its own verification item) unmet.

The plan also does not say whether the existing un-closed BAFU client changes. Leaving BAFU as-is is consistent with "keep BAFU as the default", but it needs saying, because an implementer adding a `finally` will have to decide.

**Smallest sufficient correction:** in T3's In/Out, name the teardown scope explicitly (e.g. "exception-safe close of the flow-constructed HTTP client around the fetch step") and state that the existing BAFU client lifecycle is unchanged.

---

### 3. Low — a DHM river without `water_level` is classified as a configuration failure, which permanently degrades fetch health for Plan 268's six gauges if they are ever promoted

**Location:** `300:88-90` and `300:169-171`; cross-reference `300:426-427`.

Decision 2 requires `measured_parameters` to contain `water_level` for a supported DHM fetch, and decision 7 makes its absence a `CONFIGURATION_ERROR` outcome rather than an unsupported-station skip. Plan 268 — which Plan 300 names — imports six DHM river gauges carrying **daily discharge and no level data at all**, and `268:676-679` records that they are deliberately held at `station_status = onboarding`, with promotion still an open call in that plan. If they are promoted under a DHM-selected deployment they become DHM + RIVER + GAUGED + operational without `water_level`, so each poll emits a `CONFIGURATION_ERROR` and `_fetch_health_status` (`flows/ingest_observations.py:201-205`) reports WARNING indefinitely — for six stations that are correctly configured and simply outside this adapter's scope.

The plan's § Remaining deployment inputs covers this only obliquely ("Correctly eligible station rows (decision 2)"). The specific cross-plan consequence is unowned.

**Smallest sufficient correction:** add one line to decision 2 or § Remaining deployment inputs stating that a DHM river lacking `water_level` is a configuration failure, so Plan 268's six discharge-only gauges must not be promoted to `operational` under a DHM-selected deployment, and naming which plan owns that call.

---

### 4. Low — T3's In/Out does not enumerate the six documents its Verification mandates

**Location:** In/Out `300:344-350` ("affected configuration/protocol docs") vs Verification `300:397-402`.

The Verification requires updates to `docs/conventions.md`, `docs/spec/types-and-protocols.md`, `docs/spec/config-reference.toml`, `docs/touchpoint-maps.md`, `docs/design/v0-flow2-observation-pipeline.md` and the captured-example README. Of these, only the first three plausibly read as "configuration/protocol docs"; `docs/touchpoint-maps.md` and the Flow 2 design doc do not. `docs/workflow.md` § Plan Structure requires In/Out to state bounded files.

**Smallest sufficient correction:** list the six paths in T3's In.

---

### Checks I could not complete

- **No commands run.** No tests, `ruff`, `pyright`, `git`, or network calls — read-only tools only, as instructed. I relied on reading the code rather than executing it.
- **Plan SHA256 not verified.** No hashing tool is available in this tool set; I reviewed `docs/plans/300-dhm-observation-adapter.md` as it stands in this worktree at `ed2da78a`.
- **`original-responses.zip` not inspected.** No extraction tool available. I checked the readable JSON captures and `manifest.json` (which records both `sha256` and `stored_sha256`) instead; the record counts, `count` placeholder, `next`-on-empty-page and lower-bound-inclusion claims were all confirmed against the readable files.
- **Earlier/concurrent reviews not read,** per instruction. A repo-wide grep for `_cursor_parameter_for_kind` incidentally printed three lines from `docs/reviews/300-dhm-observation-adapter/claude-review.md` in tool output; I did not open the file and did not use those lines. The findings above are my own.
- **Direct DHM host, auth and network** were not assessed — pending deployment inputs per session authority, and I did not seek duplicate contract evidence.
- **`day.json` record count** was confirmed by counting `"waterLevelOn":` occurrences against `manifest.json`'s `returned_rows: 118`; I did not parse the file structurally (it is a single 40k-token minified line).

**Verdict: not CLEAN — four findings (2 Medium, 2 Low).** None is a correctness defect in the adapter design itself; all four are boundary/ownership gaps. The parsing, pagination, watermark, outcome-taxonomy and QC-interval decisions check out against the repository and the captures, and the scope is proportionate — in particular, the "conflicting values fail the station" rule is justified rather than defensive, because `store/observation_store.py:255-263` would otherwise silently last-wins on a natural-key collision, and decision 3's finite-value requirement prevents `_validate_raw_observation` (`:266-270`) from aborting the whole flow from inside the unguarded `_store_raw_task` call at `:673`.
