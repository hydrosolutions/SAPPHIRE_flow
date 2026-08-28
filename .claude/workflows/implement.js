export const meta = {
  name: 'implement',
  description: 'Build a READY plan, then GATE the diff with an iterative independent Codex review. An implementer locks the plan’s key acceptance criteria RED-FIRST (from the spec, failing as a real assertion — the best of WF2, done Plan-105-safely), follows the phase graph, runs the exit gates, and commits locally (hold-at-PR: it never pushes or merges). Then a REAL independent Codex CLI pass (codex exec -s read-only over git diff) plus a Claude design reviewer critique the COMMITTED diff each round; a fixer resolves every blocker+major and re-commits; loops UNTIL clean or ESCALATES. Proves test-soundness (a locking test must fail against the buggy code). The human owns the PR + merge.',
  phases: [
    { title: 'Implement' },
    { title: 'Review loop' },
    { title: 'Finalize' },
  ],
}

// ── WHY THIS EXISTS ──────────────────────────────────────────────────────────
// Post-implementation review is already MANDATORY (docs/workflow.md: post-impl gate +
// post-WF2 adversarial Codex rounds). But running it was manual: hand-write a codex
// prompt against the diff, launch, babysit, loop. `implement` makes that reflexive —
// the independent Codex review of the committed diff is a baked-in stage, same idiom as
// the `plan` workflow. A green test suite + one review pass hides real bugs (Plan 105:
// a second adversarial Codex round caught 3 blockers 159 green tests missed), so this
// loops-until-converge and proves each locking test FAILS against the buggy code.
//
// BEST-OF-BOTH with the old WF2/vision-build: WF2's distinctive value was authoring the
// acceptance test FIRST, from the spec (red-first), so the test locks the REQUIREMENT
// independently of the implementation. WF2's auto-authoring BROKE on Plan 105 (tests
// against a not-yet-existing/changing signature ERRORED instead of failing RED, so the
// "prove it's red" gate never converged; the team fell back to hand-authoring). `implement`
// keeps that red-first discipline for the plan's KEY acceptance criteria, Plan-105-safely:
// a not-yet-existing symbol must fail as a RED ASSERTION, never an import/collection error;
// if it can't, the workflow ESCALATES for a human to hand-author rather than skipping.
//
// ── USAGE ────────────────────────────────────────────────────────────────────
// Workflow({ name: 'implement', args: { planPath: 'docs/plans/NNN-....md', repo, baseBranch: 'main', maxRounds: 3 } })
//   planPath   (required)  a READY plan doc to implement.
//   repo       (optional)  repo root (default '.').
//   baseBranch (optional)  the branch the diff is measured against (default 'main').
//   maxRounds  (optional)  max review↔fix rounds before ESCALATION (default 5).
//   codexTimeoutMs (optional) per-Codex-call Bash timeout (default 600000).
// Returns: { planPath, rounds, converged, stalled, exhausted, escalated,
//            escalationReason, residualBlockerCount, residualMajorCount,
//            residualFindings, codexFailedRounds, implementerReport, final }.
//
// hold-at-PR (HARD): the workflow COMMITS locally on the CURRENT branch and STOPS. It
//   NEVER pushes, opens a PR, or merges — the human does that after reading the result.
//   Run it on a dedicated branch/worktree that already has the READY plan in the tree.

// ── args ─────────────────────────────────────────────────────────────────────
let A = args || {}
if (typeof A === 'string') {
  try { A = JSON.parse(A) } catch (_e) { A = {} }
}
const planPath = A.planPath
const repo = A.repo || '.'
const baseBranch = A.baseBranch || 'main'
const maxRounds = A.maxRounds || 5
const codexTimeoutMs = A.codexTimeoutMs || 600000
if (!planPath) {
  throw new Error('implement requires args.planPath (a READY plan doc to build)')
}

// Normalize a value to a git SHA (trimmed, lowercase, 7–40 hex) or null. A noisy/failed
// HEAD capture must NOT string-compare as "fresh" against another noisy value.
const asSha = (s) => {
  const t = typeof s === 'string' ? s.trim().toLowerCase() : ''
  return /^[0-9a-f]{7,40}$/.test(t) ? t : null
}

const IMPL_REPORT = {
  type: 'object',
  required: ['changedFiles', 'commandsRun', 'deviations', 'residualRisks', 'committed', 'exitGatesPassed', 'testSoundnessProved', 'acceptanceTestsRedFirst'],
  properties: {
    changedFiles: { type: 'array', items: { type: 'string' } },
    commandsRun: { type: 'array', items: { type: 'string' } }, // exit-gate cmds + their pass/fail
    deviations: { type: 'array', items: { type: 'string' } },  // where the impl departed from the plan + why
    residualRisks: { type: 'array', items: { type: 'string' } },
    committed: { type: 'boolean' },                            // did it land a local commit (NOT pushed)
    commitSha: { type: 'string' },
    exitGatesPassed: { type: 'boolean' },                      // ruff + pyright + pytest ALL green
    testSoundnessProved: { type: 'boolean' },                 // new locking tests shown to FAIL against buggy code
    lockingTestProofs: { type: 'array', items: { type: 'string' } }, // which tests, proven fail-against-buggy
    // Best-of-both with the old WF2/vision-build: the plan's KEY acceptance criteria were
    // authored as tests FIRST (red-first, from the SPEC) and shown to fail RED before
    // implementing to green — OR there were no testable acceptance criteria. FALSE means
    // it could NOT be done cleanly (e.g. Plan-105 signature churn where a not-yet-existing
    // symbol ERRORS rather than fails RED) → escalate for a human to hand-author.
    acceptanceTestsRedFirst: { type: 'boolean' },
    acceptanceTests: { type: 'array', items: { type: 'string' } }, // which acceptance criteria were locked red-first
  },
}

// Independent verification of the implementer's self-report — the workflow must not
// trust "committed/gates passed", it re-checks against git + re-runs the gates.
const VERIFY = {
  type: 'object',
  required: ['headSha', 'diffNonEmpty', 'worktreeClean', 'gatesPassed'],
  properties: {
    headSha: { type: 'string' },
    diffNonEmpty: { type: 'boolean' }, // git diff <base>...HEAD has real changes
    worktreeClean: { type: 'boolean' }, // nothing uncommitted left behind
    gatesPassed: { type: 'boolean' },  // ruff + pyright + pytest re-run green
    notes: { type: 'string' },
  },
}

// reviewerFailed: a Codex pass that could not produce a verdict (see plan.js).
// rawVerdict: the Codex relay's UNEDITED codex output, so a human can audit that the
// relay did not silently launder/soften Codex's findings while "transcribing".
const FINDINGS = {
  type: 'object',
  required: ['findings'],
  properties: {
    reviewerFailed: { type: 'boolean' },
    rawVerdict: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['severity', 'issue', 'location', 'suggestion'],
        properties: {
          severity: { type: 'string', enum: ['blocker', 'major', 'minor'] },
          issue: { type: 'string' },
          location: { type: 'string' }, // file:line in the DIFF the finding is grounded in
          suggestion: { type: 'string' },
        },
      },
    },
  },
}

// The fixer's structured self-report — so the loop can GATE on test-soundness after a
// fix round (the git/gate VERIFY below is non-mutating and cannot re-prove soundness).
const FIX_REPORT = {
  type: 'object',
  required: ['changelog', 'testSoundnessProved'],
  properties: {
    changelog: { type: 'string' },
    testSoundnessProved: { type: 'boolean' }, // every blocker/bug-fix locking test shown to fail-against-buggy (or none needed)
    lockingTestProofs: { type: 'array', items: { type: 'string' } },
    disputedFindings: { type: 'array', items: { type: 'string' } }, // findings judged wrong + why (not complied with)
  },
}

const FINAL = {
  type: 'object',
  required: ['summary', 'recommendation'],
  properties: {
    summary: { type: 'string' },
    residualRisks: { type: 'array', items: { type: 'string' } },
    recommendation: { type: 'string', enum: ['PR-READY', 'NOT-READY'] },
  },
}

// The independent Codex reviewer prompt — adversarial, over the COMMITTED diff.
function codexDiffPrompt(round) {
  return (
    `CRITICAL, ADVERSARIAL, repo-grounded review (round ${round}) of the COMMITTED implementation of the ` +
    `plan at ${planPath} in repo ${repo}. First read the plan, then read the diff: ` +
    `\`git diff ${baseBranch}...HEAD\` (and \`git diff ${baseBranch}...HEAD --stat\`). You are INDEPENDENT — ` +
    `assume the diff is subtly wrong until you prove otherwise. Hunt for: correctness bugs and regressions ` +
    `from the change, edge cases, SILENT-FAILURE modes, missed callers/config/migrations, ForecastInterface ` +
    `contract breaks, deviations from the READY plan, unintended files changed, and whether the added/locked ` +
    `TESTS would actually catch a subtly-broken impl (not merely pass). Verify claims with your own Read+Grep. ` +
    `Only GENUINE problems. For each: severity (BLOCKER/MAJOR/MINOR), the file:line in the diff, and a ` +
    `concrete fix. Output a VERDICT line, then BLOCKERS / MAJORS / MINORS with file:line + fix, then a ` +
    `CONFIRMED list of what you verified correct. Do NOT edit any file.`
  )
}

// ── STALENESS GATE (Plan 200) ────────────────────────────────────────────────
// PR #201 postmortem: a plan-doc correction made AFTER a build started, and never
// pushed, could not reach the running branch before it merged — the branch built
// (faithfully) from a plan an hour stale. A preflight-only check would NOT have
// caught it (nothing was stale when the build started); the gate must also run at
// FINALIZE, the only point where the workflow could still have seen the correction.
//
// D1 — CONTAINMENT, not equality, against BOTH origin/main and local main. Plain
// equality false-escalates on every legitimate run (`/plan` edits the plan doc in
// place by design). The predicate: the branch must CONTAIN the latest plan-changing
// commit from each ref. Escalate only when that commit is ABSENT from the branch
// AND the branch's own copy of the plan differs from that ref's version — the
// signature of a correction the branch never saw, while still allowing edits the
// branch itself made on top of the latest plan.
//
// NOTE — this check is pinned to `main`/`origin/main` specifically, INDEPENDENT of
// `baseBranch` (below): plan docs in this repo are committed directly to `main`, never
// to a feature `baseBranch` (see CLAUDE.md "Plans direct to main; PRs for code"), so
// `main` is the only ref a plan correction can land on. `checkBaseBehind`, further
// down, IS parameterized on `baseBranch` — that check is about the actual diff base,
// which legitimately can differ from `main`.
//
// The content comparison is against the WORKTREE file, never HEAD's committed blob:
// a fixer/planner round can edit files without an intervening commit, so at FINALIZE
// the on-disk file can already differ from HEAD. Diffing HEAD's blob against a ref
// would false-escalate when the worktree independently already matches the ref, and
// could equally miss real staleness if the worktree drifted from a HEAD that still
// happens to match the ref.
//
// D3 — fetch origin FIRST; a failed fetch, or ANY other git command erroring instead
// of returning one of its EXPECTED results, fails CLOSED (checkOk=false) rather than
// silently reading as "not stale" (an unavailable check must not resemble a passing
// one). `git log -1 -- <path>` returning a SHA does NOT mean the path exists now — a
// deleted/archived plan still has a deletion commit — so existence is checked with
// `cat-file -e` against the ref's current tip, not `log`.
const STALE_PLAN = {
  type: 'object',
  required: ['fetchOk', 'checkOk', 'stale', 'staleRefs'],
  properties: {
    fetchOk: { type: 'boolean' },
    checkOk: { type: 'boolean' }, // false = a git command errored; treat as unverifiable, NOT as "not stale"
    stale: { type: 'boolean' },   // informational only — the caller derives staleness from staleRefs itself
    staleRefs: { type: 'array', items: { type: 'string' } },
    notes: { type: 'string' },
  },
}
// NOTE — this gate is deliberately MAIN-ONLY, and does not follow `baseBranch`.
// Plan documents live on `main` (docs/plans/), so `main`/`origin/main` is where a
// plan correction lands regardless of which branch a diff is measured against. A
// run invoked with `baseBranch: 'release-v1'` still checks the plan against main,
// which is intended: the spec's authority is main, not the integration target.
function stalePlanPromptText(stage) {
  return (
    `Check whether the CURRENT branch in repo ${repo} has gone stale against the plan doc at ${planPath}, ` +
    `at the ${stage} stage of a build. Throughout, distinguish an EXPECTED git result (empty output, "not an ` +
    `ancestor", "no diff") from a git COMMAND ERROR (a non-zero exit for any reason OTHER than the one ` +
    `documented expected-negative below) — a command error means the check could NOT run and MUST fail closed ` +
    `(checkOk=false), never be read as "not stale". Steps:\n` +
    `1. Run \`git -C ${repo} fetch origin main --quiet\`. If it FAILS (offline/auth/no such remote/etc.), STOP ` +
    `and return {"fetchOk": false, "checkOk": false, "stale": false, "staleRefs": [], "notes": "<what failed>"} ` +
    `— do NOT compare against a possibly-stale origin/main.\n` +
    `2. Check whether ${planPath} exists in the CURRENT WORKTREE right now: \`test -f ${repo}/${planPath}\`.\n` +
    `3. For EACH of these two refs — "origin/main" and "main" — first check whether the plan file exists AT ` +
    `THAT REF'S CURRENT TIP: \`git -C ${repo} cat-file -e <ref>:${planPath}\` (exit 0 = exists there now, ` +
    `non-zero = absent there now). Do NOT use \`git log -1 -- <path>\` non-empty output as a proxy for "exists" ` +
    `— a plan later DELETED or ARCHIVED (e.g. moved to docs/plans/archive/) still has history touching the ` +
    `path, so \`log\` finds its deletion commit even though the blob is gone at that ref now; only \`cat-file ` +
    `-e\` tells you whether it exists THERE NOW.\n` +
    `   - ABSENT at the ref AND ABSENT in the worktree (step 2): consistent, SKIP this ref (not stale for it).\n` +
    `   - ABSENT at the ref but PRESENT in the worktree: that ref no longer has this plan (likely archived or ` +
    `moved upstream) while the branch still does — record this ref in staleRefs with a note that it was ` +
    `removed there; do NOT attempt a content diff against a blob that no longer exists.\n` +
    `   - PRESENT at the ref: continue to step 4.\n` +
    `4. Find the latest commit on that ref that touched the file: \`git -C ${repo} log -1 --format=%H <ref> -- ` +
    `${planPath}\`. Step 3 already confirmed the blob exists at the ref's tip, so this MUST return a SHA; if it ` +
    `errors or returns empty anyway, that is a COMMAND ERROR — checkOk=false.\n` +
    `5. Check containment: \`git -C ${repo} merge-base --is-ancestor <sha> HEAD\`. Exit 0 = contained (not ` +
    `stale for this ref). Exit 1 = NOT contained — this is the EXPECTED negative, not an error; go to step 6. ` +
    `Any OTHER exit code (e.g. an invalid SHA) is a COMMAND ERROR — checkOk=false.\n` +
    `6. If NOT contained, compare the ACTUAL WORKTREE FILE — not HEAD's last commit; the current branch may ` +
    `have UNCOMMITTED edits to the plan — against that ref's content: \`git -C ${repo} diff <ref> -- ` +
    `${planPath}\`. An EMPTY diff (exit 0, no output) means the worktree copy ALREADY matches that ref's ` +
    `content — NOT stale for it (a branch whose own working copy already carries an equivalent edit must not ` +
    `false-escalate), even if the committed HEAD blob differs. A NON-EMPTY diff means that ref is STALE against ` +
    `what is actually on disk — record its name ("origin/main" or "main") in staleRefs. Plain \`git diff\` (no ` +
    `--exit-code) exits non-zero ONLY on a real error, never merely for having a diff — so any non-zero exit ` +
    `here is a COMMAND ERROR — checkOk=false.\n` +
    `Return fetchOk=true, checkOk=(false if ANY step above hit a command error, true otherwise), ` +
    `stale=(staleRefs is non-empty) — informational; the caller derives staleness from staleRefs itself, not ` +
    `from this field — staleRefs, and notes explaining what you found (the SHAs compared, which ref(s) were ` +
    `stale vs current, and why).`
  )
}
// D5 — a stale BASE (the branch is behind origin/${baseBranch}) WARNS and proceeds; it
// never escalates. A hard refusal fires on essentially every branch (a base branch
// moves several times a day) and would be disabled within a day — the same failure as
// a blocking commit hook (D4). This is deliberately weaker than a refusal to a stale
// PLAN. Unlike the plan-staleness check above, this IS parameterized on `baseBranch`
// (default 'main') — it measures drift against the actual branch the diff will be
// compared to, which the caller may override.
const BASE_BEHIND = {
  type: 'object',
  required: ['behindCount'],
  properties: { behindCount: { type: 'number' }, notes: { type: 'string' } },
}
async function checkBaseBehind(phaseTitle) {
  const baseBehind = await agent(
    `In repo ${repo}, fetch the base branch fresh (\`git -C ${repo} fetch origin ${baseBranch} --quiet\`; if ` +
    `that fails, proceed with whatever origin/${baseBranch} is already known locally) and report how many ` +
    `commits origin/${baseBranch} has that the current branch (HEAD) lacks: \`git -C ${repo} rev-list --count ` +
    `HEAD..origin/${baseBranch}\`. Return behindCount as a number (0 if the command errors, e.g. no such ` +
    `remote-tracking ref). Do not edit anything.`,
    { label: 'base-behind', phase: phaseTitle, model: 'sonnet', effort: 'low', schema: BASE_BEHIND },
  )
  if (baseBehind && baseBehind.behindCount > 0) {
    log(`⚠️ WARN — this branch is ${baseBehind.behindCount} commit(s) behind origin/${baseBranch}. Proceeding, ` +
        `but review against the current diff and run \`git merge origin/${baseBranch}\` before opening the PR.`)
  }
  return baseBehind
}
// The caller derives staleness itself from fetchOk/checkOk/staleRefs — never trusting
// the agent-reported `stale` boolean on its own, since a structurally-valid-but-wrong
// {stale:false} alongside a non-empty staleRefs must still be treated as stale.
function isPlanStale(check) {
  return !check || !check.fetchOk || !check.checkOk || !Array.isArray(check.staleRefs) || check.staleRefs.length > 0
}
function staleEscalation(check, stage) {
  if (!check || !check.fetchOk) {
    return `plan-staleness check could not run at ${stage} (${check?.notes || 'git fetch failed'})`
  }
  if (!check.checkOk) {
    return `plan-staleness check hit a git error at ${stage} and produced no trustworthy verdict — treating as stale (${check.notes || 'see notes'})`
  }
  return `plan at ${planPath} is STALE at ${stage} against ${(check.staleRefs || []).join(', ')}: ${check.notes || ''}`
}

phase('Implement')

// PREFLIGHT — NEVER implement a plan that is not READY. A DRAFT is a proposal, not an
// instruction (CLAUDE.md trust hierarchy); building one silently is a real hazard.
const PREFLIGHT = {
  type: 'object',
  required: ['status', 'isReady'],
  properties: { status: { type: 'string' }, isReady: { type: 'boolean' } },
}
const preflight = await agent(
  `Read ONLY the YAML frontmatter and the Status section of the plan at ${planPath} (repo ${repo}). ` +
  `Return its 'status:' value and isReady=true ONLY if that status is exactly READY (not DRAFT/SPLIT/etc.).`,
  { label: 'preflight-status', phase: 'Implement', model: 'sonnet', effort: 'low', schema: PREFLIGHT },
)
if (!preflight || !preflight.isReady) {
  log(`⚠️ ESCALATION — ${planPath} is not READY (status=${preflight?.status || 'unknown'}). ` +
      `Refusing to implement a non-READY plan.`)
  return {
    planPath, rounds: 0, converged: false, stalled: false, exhausted: false,
    escalated: true, escalationReason: `plan is not READY (status=${preflight?.status || 'unknown'})`,
    residualBlockerCount: 0, residualMajorCount: 0, residualFindings: [], codexFailedRounds: 0,
    implementerReport: null, verify: null, final: null,
  }
}

// PREFLIGHT STALENESS — D1/D3/D5 (Plan 200). Runs AFTER the READY check (no point
// staleness-checking a plan that isn't buildable yet) and BEFORE any implementation work.
const staleAtPreflight = await agent(
  stalePlanPromptText('PREFLIGHT'),
  { label: 'stale-plan-preflight', phase: 'Implement', model: 'sonnet', effort: 'low', schema: STALE_PLAN },
)
if (isPlanStale(staleAtPreflight)) {
  const reason = staleEscalation(staleAtPreflight, 'PREFLIGHT')
  log(`⚠️ ESCALATION — ${reason}. Refusing to build from an unverifiable or superseded spec (PR #201 postmortem).`)
  return {
    planPath, rounds: 0, converged: false, stalled: false, exhausted: false,
    escalated: true, escalationReason: reason,
    residualBlockerCount: 0, residualMajorCount: 0, residualFindings: [], codexFailedRounds: 0,
    implementerReport: null, verify: null, final: null,
  }
}
await checkBaseBehind('Implement')

// Capture HEAD BEFORE implementing, so verification can prove a NEW commit actually
// landed — a false `committed:true` over a pre-existing/stale branch diff must NOT pass.
const PREHEAD = { type: 'object', required: ['headSha'], properties: { headSha: { type: 'string' } } }
const preHeadRes = await agent(
  `Run 'git rev-parse HEAD' in repo ${repo} and return headSha. Do NOT edit, stage, or commit anything.`,
  { label: 'pre-head', phase: 'Implement', model: 'sonnet', effort: 'low', schema: PREHEAD },
)
const preHead = preHeadRes && preHeadRes.headSha ? preHeadRes.headSha : null

// The independent verification prompt — reused after the initial build AND after every
// fixer round (a fixer can break gates or skip a proof just as an implementer can).
const verifyPromptText =
  `Independently VERIFY the committed state in repo ${repo} — trust nothing that was claimed. ` +
  `Run and report: 'git rev-parse HEAD' (headSha); 'git diff ${baseBranch}...HEAD --stat' — is there a REAL ` +
  `non-empty diff (diffNonEmpty)?; 'git status --porcelain' — is the worktree CLEAN with nothing uncommitted ` +
  `left (worktreeClean)?; and RE-RUN THE EXIT-GATE COMMANDS EXACTLY AS THE PLAN'S "Exit gates" SECTION ` +
  `SPECIFIES (read ${planPath} for them) — typically ruff check + ruff format --check + a pyright check + ` +
  `pytest. **CRITICAL — judge pyright by the REPO'S policy, not raw exit code:** this repo gates pyright by a ` +
  `RATCHET against a baseline (run it the repo's way, e.g. 'uv run pyright --outputjson src/ > live.json && ` +
  `uv run python tools/pyright_ratchet.py live.json tools/pyright_baseline.json'). Hundreds of PRE-EXISTING ` +
  `errors are expected; a non-zero raw 'pyright src/' caused ONLY by pre-existing baseline errors (no NEW ` +
  `errors introduced by this diff) is a PASS, not a failure. **PYTEST TAKES SEVERAL MINUTES (this repo has ` +
  `~2000 tests, ~4+ min):** run it in the FOREGROUND to completion with a GENEROUS Bash timeout (e.g. ` +
  `600000ms) and WAIT for the actual pass/fail line — do NOT background it and give up / submit early. You ` +
  `MUST observe the final pytest summary before reporting; an unfinished/unobserved pytest is NOT a pass, but ` +
  `waiting the full duration is expected and required, not a reason to bail. Report gatesPassed=true ONLY if ` +
  `every gate passes by the repo's ACTUAL convention. Put the exact commands you ran + their outcomes ` +
  `(including the final pytest summary line) in notes. Do NOT edit, stage, commit, push, or merge anything.`

const implementerReport = await agent(
  `You are the IMPLEMENTER of the READY plan at ${planPath} (repo ${repo}). Read the plan and its dependency ` +
  `graph, then implement it FOLLOWING THE PHASE ORDER (respect task_depends_on; do dependent phases in sequence). ` +
  `RED-FIRST ACCEPTANCE TESTS (borrowed from WF2, done Plan-105-safely): for the plan's KEY acceptance ` +
  `criteria, author the test(s) FROM THE SPEC **first** and confirm each fails RED — a genuine ASSERTION ` +
  `failure, NOT a collection/import ERROR. If a target symbol does not exist yet, write the test so it still ` +
  `fails as a RED assertion (guard the import and assert the expected behavior), never an ImportError. If a ` +
  `criterion CANNOT be made to fail cleanly red (e.g. signature churn — the Plan-105 trap), set ` +
  `acceptanceTestsRedFirst=false and NOTE which criteria in acceptanceTests — but STILL complete the ` +
  `implementation (do NOT stop): the independent diff review will scrutinize test meaningfulness and a human ` +
  `is flagged. Otherwise implement to make the red tests green. (If the plan has no testable acceptance ` +
  `criteria, acceptanceTestsRedFirst=true.) ` +
  `Obey CLAUDE.md: type hints, frozen dataclasses at boundaries, structlog, no bare except, tests for new behavior. ` +
  `UPDATE every affected doc (CLAUDE.md §Documentation Hygiene: no code change without its doc sync — specs, ` +
  `conventions, touchpoint maps as relevant). Then run the plan's exit gates (from the plan's "Exit gates" ` +
  `section — typically ruff check + ruff format --check + a pyright check + pytest) and make them pass. For ` +
  `PYRIGHT, use the repo's RATCHET (e.g. tools/pyright_ratchet.py vs baseline) — hundreds of PRE-EXISTING ` +
  `errors are expected and are NOT the gate; only NEW errors introduced by your diff fail it. For each ` +
  `locking test of a CORRECTNESS/BUG FIX (not every feature test), PROVE it is sound: ` +
  `confirm it FAILS against the buggy/absent code (stash the impl, keep the test, run it, expect RED), then ` +
  `restore. This is CODE, so follow the FULL mandatory version workflow (CLAUDE.md §Version Bumping): ` +
  `uv run bump-my-version bump patch; STAGE the version files with your changes; commit with a conventional ` +
  `message on the CURRENT branch. **DO NOT TAG** — per PR #149 the version bump goes in the commit, but the ` +
  `tag is applied on \`main\` AFTER merge, never on a feature branch (tagging a feature branch collides with ` +
  `parallel sessions, which has happened repeatedly). ` +
  `HOLD-AT-PR (HARD): commit + tag LOCALLY only — do NOT push, do NOT open a PR, do NOT merge. If a phase ` +
  `cannot be implemented as written (the plan is wrong against the real code), STOP and report the deviation ` +
  `rather than silently working around it. Return the report: changedFiles, commandsRun (each gate + ` +
  `pass/fail), deviations (with why), residualRisks, committed, commitSha, exitGatesPassed (true ONLY if ` +
  `ruff+pyright+pytest ALL green), testSoundnessProved (true if every correctness/bug-fix locking test was ` +
  `shown to fail-against-buggy, OR there were none to prove), lockingTestProofs (which tests you proved), ` +
  `acceptanceTestsRedFirst (true if the key acceptance criteria were locked red-first OR none were testable; ` +
  `false if a criterion could not be made to fail cleanly red), and acceptanceTests (which criteria you locked).`,
  { label: 'implement', phase: 'Implement', model: 'sonnet', effort: 'high', schema: IMPL_REPORT },
)
log(`Implemented: committed=${implementerReport?.committed}, sha=${implementerReport?.commitSha || '(none)'}, ` +
    `gatesPassed(self)=${implementerReport?.exitGatesPassed}, testSoundness=${implementerReport?.testSoundnessProved}, ` +
    `acceptanceRedFirst=${implementerReport?.acceptanceTestsRedFirst}, ` +
    `${(implementerReport?.changedFiles || []).length} file(s), ${(implementerReport?.deviations || []).length} deviation(s)`)

// INDEPENDENT VERIFICATION — do NOT trust the implementer's self-report of a commit or
// green gates (the whole point of this workflow is that a "done" claim is evidence, not
// proof). Re-derive HEAD, a non-empty diff, a clean worktree, re-run the gates, AND prove
// a FRESH commit landed (verify.headSha must differ from the pre-implement HEAD) so a
// stale pre-existing branch diff can never masquerade as this build's work.
const verify = await agent(
  verifyPromptText,
  { label: 'verify-impl', phase: 'Implement', model: 'sonnet', effort: 'medium', schema: VERIFY },
)
const freshCommit = !!asSha(verify?.headSha) && !!asSha(preHead) && asSha(verify.headSha) !== asSha(preHead)
const verified = !!verify && verify.diffNonEmpty && verify.worktreeClean && verify.gatesPassed && freshCommit
log(`Verify: preHead=${preHead || '(none)'}, headSha=${verify?.headSha || '(none)'}, freshCommit=${freshCommit}, ` +
    `diffNonEmpty=${verify?.diffNonEmpty}, worktreeClean=${verify?.worktreeClean}, gatesPassed=${verify?.gatesPassed}`)

// HARD gate — a genuinely unverifiable build (no commit, unsound tests, empty/stale/
// dirty diff, or gates that FAIL by the repo's real convention) aborts before review.
// Red-first is NOT here (see below): a red-first miss is a soft flag, not an abort —
// the independent diff review still adds value and should run.
if (!implementerReport || !implementerReport.committed || !implementerReport.testSoundnessProved || !verified) {
  log(`⚠️ ESCALATION — implementation not independently verified ` +
      `(committed=${implementerReport?.committed}, testSoundness=${implementerReport?.testSoundnessProved}, ` +
      `freshCommit=${freshCommit}, diffNonEmpty=${verify?.diffNonEmpty}, worktreeClean=${verify?.worktreeClean}, ` +
      `gatesPassed=${verify?.gatesPassed}). A human must intervene before review — do NOT proceed to the diff ` +
      `review over an unverified/empty/stale/failing diff.`)
  return {
    planPath, rounds: 0, converged: false, stalled: false, exhausted: false,
    escalated: true, escalationReason: 'implementation not independently verified (fresh commit / non-empty diff / clean worktree / green gates / test-soundness)',
    residualBlockerCount: 0, residualMajorCount: 0, residualFindings: [], codexFailedRounds: 0,
    implementerReport, verify, final: null,
  }
}

// SOFT flag — red-first not achieved (e.g. the Plan-105 case, or the implementer built
// test-alongside rather than test-first). Do NOT abort: `testSoundnessProved` already
// gives the "test catches the absent code" guarantee, and the independent diff review
// below is a SECOND line of defence on test meaningfulness / spec-conformance. Carry it
// as a residual risk for the human instead of skipping the review entirely.
const redFirstMissed = !implementerReport.acceptanceTestsRedFirst
if (redFirstMissed) {
  log(`⚠️ Red-first acceptance NOT achieved (acceptanceTestsRedFirst=false) — proceeding to the independent ` +
      `Codex-diff review, which scrutinises test meaningfulness; surfacing as a residual risk. ` +
      `(If this is the Plan-105 case, a human should hand-author the locked test before merge.)`)
  // Added after Plan 152: a gate that will NOT go red is not always an implementation
  // slip — it is often a PLAN defect, where the plan asked for behavior the repo already
  // has, so there is no fix to stash and nothing to prove. That distinction changes who
  // must act (plan author vs implementer), so name it explicitly rather than filing every
  // case under "implementer built test-alongside".
  log(`   ↳ DISTINGUISH THE CAUSE before treating this as an implementation slip: if the asserted behavior ` +
      `ALREADY EXISTS in the repo, this is a PLAN DEFECT (the task specified a gate that gates nothing) — ` +
      `report it against the plan, do not paper over it by weakening or relocating the test. If the behavior ` +
      `is genuinely absent and the test still would not go red, it is a test-authoring problem.`)
}

phase('Review loop')
let round = 0
let prevOpen = Infinity
let lastFindings = []
let converged = false
let stalled = false
let fixerUnverified = false
let codexFailedRounds = 0
let lastVerifiedHead = asSha(verify.headSha) // advances each time a fixer commit is independently verified
while (round < maxRounds) {
  round += 1

  // ONE required independent Codex pass over the committed diff + a Claude design
  // reviewer checking the diff against the plan. Distinct focuses, in parallel.
  const reviewThunks = [
    () => agent(
      `You are a RELAY for an INDEPENDENT Codex review of a COMMITTED diff — you add NO opinions; you run Codex ` +
      `and translate its verdict verbatim.\n\n` +
      `STEP 1 — write this exact prompt to a scratch file (heredoc, safe quoting) and run it, giving the Bash ` +
      `call a timeout of ${codexTimeoutMs}ms so a hung CLI cannot stall the workflow:\n` +
      `  ./scripts/codex-review.sh <scratch-file>\n` +
      `(run the SCRIPT — do NOT hand-roll a \`codex exec\` call. The script owns the mandatory ` +
      `\`< /dev/null\` redirect; without it \`codex exec\` writes its whole review, then blocks on ` +
      `"Reading additional input from stdin..." until your timeout kills it — the verdict is produced ` +
      `and thrown away, and the round silently has NO independent reviewer. That is exactly what ` +
      `happened on 2026-08-28, twice in one run, when this instruction was prose instead of a script. ` +
      `A non-zero exit from the script means NO usable verdict.)\n` +
      `The prompt is:\n<<<CODEX_PROMPT\n${codexDiffPrompt(round)}\nCODEX_PROMPT\n\n` +
      `STEP 2 — a Bash TIMEOUT, NON-ZERO exit, empty output, or a startup/hang message ALL count as NO usable ` +
      `verdict. In any of those, KILL and retry ONCE. If still nothing, return {"reviewerFailed": true, ` +
      `"findings": []} — never invent findings, never return a clean empty result for a dead reviewer.\n\n` +
      `STEP 3 — map Codex's BLOCKERS/MAJORS/MINORS into 'findings' (issue/location=file:line/suggestion), ` +
      `relaying FAITHFULLY — no drop/soften/upgrade/add. ALSO return 'rawVerdict' = Codex's UNEDITED output ` +
      `verbatim (so a human can audit the transcription). Clean review → {"findings": [], "rawVerdict": "..."} ` +
      `without reviewerFailed.`,
      { label: `codex-review-r${round}`, phase: 'Review loop', model: 'sonnet', effort: 'low', schema: FINDINGS },
    ),
    () => agent(
      `You are a Claude DESIGN reviewer of the COMMITTED implementation of ${planPath} (repo ${repo}). ` +
      `Read the plan, then \`git diff ${baseBranch}...HEAD\`. Check: the patch matches the approved plan; ` +
      `requirements + non-goals respected; user-visible behavior correct; no unresolved design decision made ` +
      `silently; the tests are MEANINGFUL (would fail on a subtly-broken impl), not just green. Cite file:line ` +
      `in the diff. Return ONLY genuine blocker/major/minor findings with concrete fixes; empty if sound.`,
      { label: `design-review-r${round}`, phase: 'Review loop', model: 'sonnet', effort: 'high', schema: FINDINGS },
    ),
  ]
  // Per-slot accounting BEFORE filtering (slot 0 is always the independent Codex relay).
  // A reviewer is UNUSABLE if it died (null) or signaled reviewerFailed; either way the
  // panel is incomplete and the loop must not falsely converge.
  const rawReviews = await parallel(reviewThunks)
  const usable = rawReviews.map((r) => !!r && r.reviewerFailed !== true)
  const lost = usable.filter((u) => !u).length
  const codexFailed = !usable[0]
  if (codexFailed) codexFailedRounds += 1
  if (lost > 0) log(`Round ${round}: WARNING — ${lost} reviewer(s) incomplete (null or reviewerFailed).`)
  if (codexFailed) log(`Round ${round}: WARNING — the independent Codex pass produced no verdict (CLI hang/error).`)

  const reviews = rawReviews.filter((r, i) => usable[i])
  const findings = reviews.flatMap((r) => r.findings || [])
  const blockers = findings.filter((f) => f.severity === 'blocker')
  const majors = findings.filter((f) => f.severity === 'major')
  const open = blockers.length + majors.length
  lastFindings = findings
  log(`Round ${round}: ${blockers.length} blocker(s), ${majors.length} major(s), ${findings.length} finding(s)`)

  if (open === 0) {
    if (lost === 0) {
      converged = true
      log(`Round ${round}: no blockers or majors, full panel (incl. Codex) reported — converged.`)
      break
    }
    log(`Round ${round}: 0 blockers/majors but ${lost} reviewer(s) incomplete — re-reviewing, not converging.`)
    continue
  }

  if (round > 1 && open >= prevOpen) {
    stalled = true
    log(`Round ${round}: no progress (open ${open} >= prev ${prevOpen}) — stopping to avoid thrash.`)
    break
  }
  prevOpen = open

  // Fixer resolves every blocker+major, PROVES test-soundness, and commits+tags.
  const fixReport = await agent(
    `You are the FIXER for the committed implementation of ${planPath} (repo ${repo}). ` +
    `Reviewers (including an INDEPENDENT Codex pass over the diff) raised:\n${JSON.stringify(findings, null, 2)}\n\n` +
    `Resolve EVERY blocker and major (minors where cheap) by editing the CODE + tests, and UPDATE any affected ` +
    `docs (CLAUDE.md §Documentation Hygiene). For each locking test of a CORRECTNESS/BUG fix, PROVE it is sound: ` +
    `confirm the test FAILS against the buggy code (stash the fix, keep the test, run it — expect RED), then ` +
    `restore. Re-run the exit gates (ruff/pyright/pytest) until green. Follow the FULL version workflow: ` +
    `uv run bump-my-version bump patch; STAGE the version files; commit on the CURRENT branch. **DO NOT TAG** ` +
    `— per PR #149 tags go on \`main\` after merge, never on a feature branch. HOLD-AT-PR: commit LOCALLY only — no ` +
    `push/PR/merge. If a finding is WRONG, do NOT comply blindly — record it in disputedFindings with why. ` +
    `Return: changelog, testSoundnessProved (true if every correctness/bug-fix locking test was shown ` +
    `fail-against-buggy, OR none needed), lockingTestProofs, disputedFindings.`,
    { label: `fix-r${round}`, phase: 'Review loop', model: 'sonnet', effort: 'high', schema: FIX_REPORT },
  )
  log(`Round ${round} fix: testSoundness=${fixReport?.testSoundnessProved}, ${String(fixReport?.changelog || '').slice(0, 240)}`)

  // INDEPENDENTLY VERIFY the fixer before the next review can converge — a fixer can break
  // the gates, skip the test-soundness proof, or fail to commit just as an implementer can.
  // Require: a FRESH commit (headSha advanced past the last verified one), clean worktree,
  // non-empty diff, re-run-green gates (via the non-mutating VERIFY agent), AND the fixer's
  // own testSoundnessProved (the two reviewers additionally check test meaningfulness each
  // round). Without this, the loop could converge over a fix that silently broke pytest or
  // skipped the fail-against-buggy proof.
  const fixVerify = await agent(
    verifyPromptText,
    { label: `verify-fix-r${round}`, phase: 'Review loop', model: 'sonnet', effort: 'medium', schema: VERIFY },
  )
  const fixFresh = !!asSha(fixVerify?.headSha) && asSha(fixVerify.headSha) !== lastVerifiedHead
  const fixVerified = !!fixVerify && fixVerify.diffNonEmpty && fixVerify.worktreeClean && fixVerify.gatesPassed &&
    fixFresh && !!fixReport && fixReport.testSoundnessProved === true
  log(`Round ${round} fix verify: headSha=${fixVerify?.headSha || '(none)'}, fresh=${fixFresh}, ` +
      `worktreeClean=${fixVerify?.worktreeClean}, gatesPassed=${fixVerify?.gatesPassed}, ` +
      `testSoundness=${fixReport?.testSoundnessProved}`)
  if (!fixVerified) {
    fixerUnverified = true
    log(`⚠️ Round ${round}: the fixer's commit FAILED independent verification ` +
        `(fresh commit=${fixFresh}, worktreeClean=${fixVerify?.worktreeClean}, gatesPassed=${fixVerify?.gatesPassed}, ` +
        `testSoundness=${fixReport?.testSoundnessProved}) — stopping; a human must intervene. Do NOT treat as converged.`)
    break
  }
  lastVerifiedHead = asSha(fixVerify.headSha)
}

const residualBlockers = lastFindings.filter((f) => f.severity === 'blocker')
const residualMajors = lastFindings.filter((f) => f.severity === 'major')
const exhausted = !converged && !stalled && round === maxRounds
const escalated = !converged
const escalationReason = converged
  ? null
  : fixerUnverified
    ? `round ${round} fixer commit failed independent verification (fresh commit / clean worktree / green gates / test-soundness) — the fix is not trustworthy`
    : stalled
      ? `stalled after ${round} round(s): a fix failed to reduce the blocker+major count (stuck)`
      : `did not converge within maxRounds=${maxRounds}: ${residualBlockers.length} blocker(s) + ${residualMajors.length} major(s) remain`
if (escalated) {
  log(`⚠️ ESCALATION — implement could NOT converge (${escalationReason}). ` +
      `Do NOT open a PR as-is. A human must resolve the residual ` +
      `${residualBlockers.length} blocker(s) + ${residualMajors.length} major(s).`)
}

phase('Finalize')

// FINALIZE STALENESS — D1 re-checked (Plan 200). The PR #201 correction landed on
// local `main` an HOUR into the run; a preflight-only check would have passed and
// then gone stale DURING the build. This is the only point left where the workflow
// can still catch it, before recommending the diff for a PR against a superseded spec.
const staleAtFinalize = await agent(
  stalePlanPromptText('FINALIZE'),
  { label: 'stale-plan-finalize', phase: 'Finalize', model: 'sonnet', effort: 'low', schema: STALE_PLAN },
)
const planWentStale = isPlanStale(staleAtFinalize)
if (planWentStale) {
  log(`⚠️ ESCALATION at FINALIZE — ${staleEscalation(staleAtFinalize, 'FINALIZE')}. Do NOT open a PR from this ` +
      `commit — the spec moved out from under it; the work is not lost, it just must not be proposed as-is.`)
}

const final = await agent(
  `Read the plan at ${planPath} and the final \`git diff ${baseBranch}...HEAD\` (repo ${repo}). ` +
  `The review loop ended: converged=${converged}, stalled=${stalled}, exhausted=${exhausted}, ` +
  `residual blockers=${residualBlockers.length}, residual majors=${residualMajors.length}, ` +
  `rounds where the Codex pass failed=${codexFailedRounds}, red-first acceptance achieved=${!redFirstMissed}, ` +
  `plan went STALE during the build=${planWentStale}. ` +
  `Return: (1) 'summary' — a <=6-line summary of what was built + how it was verified; ` +
  `(2) 'residualRisks' — anything a human PR reviewer should still eyeball` +
  (redFirstMissed ? ` — you MUST include that red-first acceptance was NOT achieved (a human should confirm the acceptance tests genuinely lock the spec, or hand-author them)` : ``) +
  (planWentStale ? ` — you MUST include that the plan went stale during the build and this diff must be rebuilt against the current spec before a PR` : ``) + `; ` +
  `(3) 'recommendation' — 'PR-READY' (ready for a HUMAN to open/approve the PR — NOT auto-mergeable) only if ` +
  `no residual blockers/majors, the exit gates passed, AND the plan did NOT go stale during the build; else 'NOT-READY'. ` +
  `Remember: hold-at-PR — you are NOT merging; only the human merges. This is advice to the PR owner.`,
  { label: 'finalize', phase: 'Finalize', model: 'sonnet', effort: 'medium', schema: FINAL },
)

// PR-READY requires ACTUAL convergence (no residual blockers/majors on a complete panel)
// AND a plan that did not go stale during the build. An escalated run — stalled,
// exhausted, a failed reviewer, or a finalize-time staleness hit — is NOT-READY no
// matter what the finalize agent inferred from a possibly-partial finding set.
if (final && (!converged || planWentStale) && final.recommendation !== 'NOT-READY') {
  log(`Overriding finalize recommendation → NOT-READY (converged=${converged}, planWentStale=${planWentStale}).`)
  final.recommendation = 'NOT-READY'
}

const finalEscalated = escalated || planWentStale
const finalEscalationReason = planWentStale
  ? (escalationReason ? `${escalationReason}; also: ${staleEscalation(staleAtFinalize, 'FINALIZE')}` : staleEscalation(staleAtFinalize, 'FINALIZE'))
  : escalationReason

return {
  planPath,
  rounds: round,
  converged,
  stalled,
  exhausted,
  escalated: finalEscalated,
  escalationReason: finalEscalationReason,
  residualBlockerCount: residualBlockers.length,
  residualMajorCount: residualMajors.length,
  residualFindings: lastFindings,
  codexFailedRounds,
  redFirstMissed,
  planWentStale,
  implementerReport,
  verify,
  final,
}
