export const meta = {
  name: 'plan',
  description: 'Iteratively review + improve a DRAFT design-plan doc via an adversarial planner↔reviewer loop where a REAL independent Codex CLI pass (codex exec -s read-only) is a REQUIRED reviewer EVERY round — alongside diverse Claude design/proportionality lenses. Runs UNTIL author+reviewers CONVERGE (no blockers AND no majors); ESCALATES loudly if it cannot within maxRounds or stalls. On convergence surfaces the residual design forks a human must decide (the grill-me). Does NOT implement — edits the plan doc in place. hold-at-PR: the caller owns the branch/PR.',
  phases: [
    { title: 'Ground' },
    { title: 'Review loop' },
    { title: 'Finalize' },
  ],
}

// ── WHY THIS EXISTS (vs plan-review) ─────────────────────────────────────────
// plan-review's reviewer panel is four SONNET lenses. In practice that loop can
// rubber-stamp a design flaw it introduced (memory: independent-review-beats-the-
// automated-loop). Across the 115b split, the thing that caught a real code-grounded
// defect in EVERY chunk was a manual, independent `codex exec` pass. `plan` bakes that
// pass in as a REQUIRED reviewer each round, so the independent Codex review is a
// reflexive must-do rather than something an operator remembers to run by hand.
//
// It deliberately keeps plan-review INTACT (a Sonnet-only variant) and lives beside it.
//
// ── USAGE ────────────────────────────────────────────────────────────────────
// Workflow({ name: 'plan', args: { planPath: 'docs/plans/NNN-....md', repo: '/abs/repo/path', maxRounds: 3 } })
//   planPath   (required)  the DRAFT plan doc to review + improve — EDITED IN PLACE.
//   repo       (optional)  repo root the reviewers read (default '.').
//   maxRounds  (optional)  max review↔revise rounds before ESCALATION (default 5).
//   codexTimeoutMs (optional) per-Codex-call Bash timeout (default 600000).
// Returns: { planPath, rounds, converged, stalled, exhausted, escalated,
//            escalationReason, residualBlockerCount, residualMajorCount,
//            residualFindings, codexFailedRounds, final }.
//
// hold-at-PR: mutates ONLY the plan doc; the CALLER owns the branch/PR. Run it on a
//   branch that has the plan doc + the code in the working tree. After it returns,
//   review the plan diff + the residual grill-me, settle those, flip Status to READY,
//   then build it SEPARATELY (e.g. the `implement` workflow).

// ── args ─────────────────────────────────────────────────────────────────────
let A = args || {}
if (typeof A === 'string') {
  try { A = JSON.parse(A) } catch (_e) { A = {} }
}
const planPath = A.planPath
const repo = A.repo || '.'
const maxRounds = A.maxRounds || 5
const codexTimeoutMs = A.codexTimeoutMs || 600000
if (!planPath) {
  throw new Error('plan requires args.planPath (the DRAFT plan doc to review + improve)')
}

// A reviewer's findings: only GENUINE problems, each grounded + actionable.
// `reviewerFailed` lets a Codex reviewer signal "I could not produce a verdict"
// (e.g. the CLI hung) — distinct from "I reviewed and found nothing", so the loop
// never FALSELY converges on a silent reviewer.
const FINDINGS = {
  type: 'object',
  required: ['findings'],
  properties: {
    reviewerFailed: { type: 'boolean' },
    rawVerdict: { type: 'string' }, // the Codex relay's UNEDITED codex output, for human audit
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['severity', 'issue', 'location', 'suggestion'],
        properties: {
          severity: { type: 'string', enum: ['blocker', 'major', 'minor'] },
          issue: { type: 'string' },
          location: { type: 'string' }, // file:line the finding is grounded in, or plan §
          suggestion: { type: 'string' },
        },
      },
    },
  },
}

const FINAL = {
  type: 'object',
  required: ['summary', 'recommendation'],
  properties: {
    summary: { type: 'string' },
    residualQuestions: { type: 'array', items: { type: 'string' } },
    recommendation: { type: 'string', enum: ['READY', 'NOT-READY'] },
  },
}

// Diverse CLAUDE lenses — the axes Codex is WEAKEST on (design judgement, over-
// engineering). Codex owns feasibility / code-grounding / completeness (below), so
// the two panels are complementary, not redundant.
const CLAUDE_LENSES = [
  'DESIGN SOUNDNESS — is the proposed approach correct, and is it the SIMPLEST correct approach? Name a better alternative if the plan ignores one. Flag internal contradictions.',
  'PROPORTIONALITY (guard against over-engineering) — is the solution more complex than the problem requires? Flag over-scope, gold-plating, speculative generality, unnecessary phases/abstractions, and REFERENCE detail that belongs in code/docstrings (it rots in a plan). Judge detail against what the artifact is FOR — not "is anything missing". Propose specific cuts. Empty findings if already lean.',
  // TEST SOUNDNESS — added after Plan 152, where TWO tasks specified red-first tests
  // that could not fail for the stated reason (the asserted behavior ALREADY existed
  // elsewhere in the code). `implement` cannot catch this: it proves a test goes red
  // when the FIX is stashed, but if the behavior already exists there is no fix to
  // stash — the defect is in the PLAN, so it must be caught here.
  'TEST SOUNDNESS — for EVERY task, take its stated red-first test / acceptance assertion and ask: **would this actually FAIL against the CURRENT repo, for the reason the plan claims?** VERIFY against real code with Read/Grep — never reason from the plan\'s prose. The error class to hunt: a test aimed at a boundary that ALREADY behaves correctly, while the claimed defect lives upstream or downstream of it (e.g. a plan asserting "the store must reject X" when the store already handles X and the bug is in the service that calls it — such a test passes on day one and gates nothing). Also flag: a test whose failure would be an import/collection ERROR rather than a red ASSERTION; an assertion that is already true today; and a task claiming to ADD behavior the repo already has. For each task report "sound" or the file:line proving it would not fail as claimed. Empty findings only if every task\'s gate genuinely fails today.',
]

// The INDEPENDENT Codex reviewer prompt — repo-grounded, adversarial, file:line.
// It is run by a thin Claude agent that shells out to `codex exec`; the agent then
// relays Codex's verdict FAITHFULLY into FINDINGS (it must not add its own opinions).
function codexReviewPrompt(round) {
  return (
    `CRITICAL, ADVERSARIAL, repo-grounded review (round ${round}) of the DRAFT plan at ${planPath} ` +
    `in repo ${repo}. You are an INDEPENDENT reviewer — assume the plan is wrong until the code proves ` +
    `it right. VERIFY every cited file:line / symbol / behavior with your own Read+Grep; flag any claim ` +
    `that assumes behavior the code does not have, any stale/wrong citation, any missed caller / test / ` +
    `migration / config / contract (ForecastInterface) / failure mode / backward-compat break, and any ` +
    `internal contradiction. Only GENUINE problems. For each: severity (BLOCKER/MAJOR/MINOR), the exact ` +
    `file:line it is grounded in, and a concrete fix. If a section is sound, say so — do not invent nits. ` +
    `Output a clear VERDICT line, then BLOCKERS / MAJORS / MINORS each with file:line + fix, then a ` +
    `CONFIRMED list of what you verified correct. Do NOT edit any file.`
  )
}

// ── STALENESS GATE (Plan 200) ────────────────────────────────────────────────
// PR #201 postmortem: a plan-doc correction made AFTER a build started, and never
// pushed, could not reach the running branch before it merged. `plan.js` had NO
// preflight at all before this — it gets the same gate `implement.js` got.
//
// D1 — CONTAINMENT, not equality, against BOTH origin/main and local main. Plain
// equality false-escalates on every legitimate run (`/plan` edits the plan doc in
// place by design — its own branch differs from `main` the moment it does its job).
// The predicate: the branch must CONTAIN the latest plan-changing commit from each
// ref. Escalate only when that commit is ABSENT from the branch AND the branch's
// own copy of the plan differs from that ref's version.
//
// The content comparison is against the WORKTREE file, never HEAD's committed blob:
// `plan.js` itself revises the plan doc in place WITHOUT committing (see the planner
// step below), so at FINALIZE the on-disk file can already differ from HEAD. Diffing
// HEAD's blob against a ref would false-escalate when the worktree independently
// already matches the ref, and could equally miss real staleness if the worktree has
// drifted away from a HEAD that still happens to match the ref.
//
// D3 — fetch origin FIRST; a failed fetch, or ANY other git command erroring instead
// of returning one of its EXPECTED results, fails CLOSED (checkOk=false) rather than
// silently reading as "not stale". `git log -1 -- <path>` returning a SHA does NOT
// mean the path exists now — a deleted/archived plan still has a deletion commit — so
// existence is checked with `cat-file -e` against the ref's current tip, not `log`.
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
    `at the ${stage} stage of a plan-review run. Throughout, distinguish an EXPECTED git result (empty output, ` +
    `"not an ancestor", "no diff") from a git COMMAND ERROR (a non-zero exit for any reason OTHER than the one ` +
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
    `   - ABSENT at the ref AND ABSENT in the worktree (step 2): consistent, SKIP this ref (not stale for it) ` +
    `— the common case for a brand-new DRAFT plan that has not reached main yet.\n` +
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
    `have UNCOMMITTED edits to the plan (e.g. a mid-review-loop revision) — against that ref's content: ` +
    `\`git -C ${repo} diff <ref> -- ${planPath}\`. An EMPTY diff (exit 0, no output) means the worktree copy ` +
    `ALREADY matches that ref's content — NOT stale for it, even if the committed HEAD blob differs. A ` +
    `NON-EMPTY diff means that ref is STALE against what is actually on disk — record its name ("origin/main" ` +
    `or "main") in staleRefs. Plain \`git diff\` (no --exit-code) exits non-zero ONLY on a real error, never ` +
    `merely for having a diff — so any non-zero exit here is a COMMAND ERROR — checkOk=false.\n` +
    `Return fetchOk=true, checkOk=(false if ANY step above hit a command error, true otherwise), ` +
    `stale=(staleRefs is non-empty) — informational; the caller derives staleness from staleRefs itself, not ` +
    `from this field — staleRefs, and notes explaining what you found (the SHAs compared, which ref(s) were ` +
    `stale vs current, and why).`
  )
}
// D5 — a stale BASE WARNS and proceeds; it never escalates. A hard refusal fires on
// essentially every branch (main moves several times a day) and would be disabled
// within a day — the same failure as a blocking commit hook (D4).
const BASE_BEHIND = {
  type: 'object',
  required: ['behindCount'],
  properties: { behindCount: { type: 'number' }, notes: { type: 'string' } },
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
const ESCALATION_SHAPE = (reason) => ({
  planPath, rounds: 0, converged: false, stalled: false, exhausted: false,
  escalated: true, escalationReason: reason,
  residualBlockerCount: 0, residualMajorCount: 0, residualFindings: [], codexFailedRounds: 0, final: null,
})

phase('Ground')

// PREFLIGHT STALENESS — D1/D3/D5 (Plan 200). `plan.js` has no READY-status preflight
// (a DRAFT plan is exactly what it is meant to work on), so this IS its preflight.
const staleAtPreflight = await agent(
  stalePlanPromptText('PREFLIGHT'),
  { label: 'stale-plan-preflight', phase: 'Ground', model: 'sonnet', effort: 'low', schema: STALE_PLAN },
)
if (isPlanStale(staleAtPreflight)) {
  const reason = staleEscalation(staleAtPreflight, 'PREFLIGHT')
  log(`⚠️ ESCALATION — ${reason}. Refusing to review from an unverifiable or superseded copy (PR #201 postmortem).`)
  return ESCALATION_SHAPE(reason)
}
const baseBehind = await agent(
  `In repo ${repo}, using the origin/main already fetched, report how many commits origin/main has that the ` +
  `current branch (HEAD) lacks: \`git -C ${repo} rev-list --count HEAD..origin/main\`. Return behindCount as ` +
  `a number (0 if the command errors, e.g. no such remote-tracking ref). Do not edit anything.`,
  { label: 'base-behind', phase: 'Ground', model: 'sonnet', effort: 'low', schema: BASE_BEHIND },
)
if (baseBehind && baseBehind.behindCount > 0) {
  log(`⚠️ WARN — this branch is ${baseBehind.behindCount} commit(s) behind origin/main. Proceeding, but ` +
      `review against the current diff and run \`git merge origin/main\` before treating the plan as READY.`)
}

// One shared grounding pass so reviewers start primed (they still re-verify live each round).
const grounding = (await agent(
  `Read the DRAFT plan at ${planPath} (repo ${repo}). Summarize CONCISELY (NO code dumps): its problem, goal, proposed design/decisions, and EVERY file:line or symbol it cites. Then verify each citation with Read/Grep — report which are accurate vs stale/wrong, and one or two gaps the plan does not address. Keep it under ~40 lines.`,
  { label: 'ground', phase: 'Ground', model: 'sonnet', effort: 'medium' },
)) || `(grounding unavailable — verify everything live against ${planPath} and the code)`

phase('Review loop')
let round = 0
let prevOpen = Infinity // blockers+majors from the prior round, for the thrash guard
let lastFindings = []
let converged = false
let stalled = false
let codexFailedRounds = 0
while (round < maxRounds) {
  round += 1

  // The reviewer panel, all in parallel: ONE required independent Codex pass + the
  // diverse Claude lenses. The Codex reviewer is a Claude agent that RUNS codex and
  // relays its verdict — this is what makes the review genuinely independent, not a
  // Claude model imitating one.
  const reviewThunks = [
    // ── the independent Codex reviewer ──────────────────────────────────────
    () => agent(
      `You are a RELAY for an INDEPENDENT Codex review — you add NO opinions of your own; you run Codex ` +
      `and translate its verdict verbatim into the schema.\n\n` +
      `STEP 1 — write this exact prompt to a scratch file (use a heredoc so quoting is safe), then run Codex ` +
      `read-only over it, capturing ALL output. Give the Bash call a timeout of ${codexTimeoutMs}ms so a hung ` +
      `CLI cannot stall the workflow:\n` +
      `  ./scripts/codex-review.sh <scratch-file>\n` +
      `(run the SCRIPT — do NOT hand-roll a \`codex exec\` call. The script owns the mandatory ` +
      `\`< /dev/null\` redirect; without it \`codex exec\` writes its whole review, then blocks on ` +
      `"Reading additional input from stdin..." until your timeout kills it — the verdict is produced ` +
      `and thrown away, and the round silently has NO independent reviewer. That is exactly what ` +
      `happened on 2026-08-28, twice in one run, when this instruction was prose instead of a script. ` +
      `A non-zero exit from the script means NO usable verdict.)\n` +
      `The prompt to give Codex is:\n<<<CODEX_PROMPT\n${codexReviewPrompt(round)}\nCODEX_PROMPT\n\n` +
      `STEP 2 — a Bash TIMEOUT, a NON-ZERO exit, empty output, or output that is only a startup/hang ` +
      `message ALL count as NO usable verdict. In any of those cases KILL the process and retry ONCE. If it ` +
      `STILL produces no usable verdict, return {"reviewerFailed": true, "findings": []} — do NOT invent ` +
      `findings and do NOT return an empty clean result (that would let the loop falsely converge on a dead ` +
      `reviewer).\n\n` +
      `STEP 3 — map Codex's BLOCKERS→blocker, MAJORS→major, MINORS→minor into 'findings' (issue = Codex's ` +
      `wording, location = the file:line Codex cited, suggestion = Codex's fix). Relay FAITHFULLY — do not ` +
      `drop, soften, upgrade, or add findings. ALSO return 'rawVerdict' = Codex's UNEDITED output verbatim (so ` +
      `a human can audit the transcription). If Codex reviewed cleanly, return {"findings": [], "rawVerdict": ` +
      `"..."} WITHOUT reviewerFailed. Read no other files; your only job is to run Codex and transcribe its verdict.`,
      { label: `codex-review-r${round}`, phase: 'Review loop', model: 'sonnet', effort: 'low', schema: FINDINGS },
    ),
    // ── the diverse Claude lenses ───────────────────────────────────────────
    ...CLAUDE_LENSES.map((lens, i) => () =>
      agent(
        `You are a HARSH, specific adversarial reviewer of the DRAFT plan at ${planPath} (repo ${repo}). ` +
        `Review it ONLY through this lens:\n${lens}\n\n` +
        `Prior grounding (may be stale after revisions — re-verify against the CURRENT plan + code):\n${grounding}\n\n` +
        `Read the actual code (Read/Grep) to verify the plan's claims; cite file:line in each finding's location. ` +
        `Return ONLY genuine problems (blocker/major/minor) with a concrete suggestion each. ` +
        `Do NOT invent nitpicks or restyle prose. If the plan is sound through your lens, return an empty findings array.`,
        { label: `review-r${round}-lens${i}`, phase: 'Review loop', model: 'sonnet', effort: 'high', schema: FINDINGS },
      ),
    ),
  ]
  // Keep the RAW (unfiltered) results so per-slot accounting is airtight. Slot 0 is
  // always the independent Codex relay. A reviewer is UNUSABLE if it died (null) or
  // signaled reviewerFailed — either way the panel is INCOMPLETE, so the loop must NOT
  // declare "clean" on it (false-convergence guard). Filtering BEFORE accounting would
  // lose the identity of which slot failed (Codex vs a lens) — so account first.
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
    // 0 open but the panel was incomplete → re-review next round, don't converge.
    log(`Round ${round}: 0 blockers/majors but ${lost} reviewer(s) incomplete — re-reviewing, not converging.`)
    continue
  }

  // Thrash guard: after a revision, the open (blocker+major) count must strictly decrease.
  if (round > 1 && open >= prevOpen) {
    stalled = true
    log(`Round ${round}: no progress (open ${open} >= prev ${prevOpen}) — stopping to avoid thrash.`)
    break
  }
  prevOpen = open

  // Planner (author) revises the doc IN PLACE, resolving every blocker + major.
  const changelog = await agent(
    `You are the PLANNER/author of the DRAFT plan at ${planPath} (repo ${repo}). ` +
    `Reviewers (including an INDEPENDENT Codex pass) raised these findings:\n${JSON.stringify(findings, null, 2)}\n\n` +
    `Revise the plan doc IN PLACE (Edit/Write ${planPath}) to resolve EVERY blocker and major, and minors where cheap. ` +
    `Do NOT re-open or regress previously-resolved findings; if a fix forces a trade-off, NOTE it in the plan rather than silently regressing elsewhere. ` +
    `If a finding is WRONG, do not comply blindly — add a one-line note in the plan explaining why. ` +
    `Preserve the plan's structure and 'Status: DRAFT'. Ground any new claim in a real file:line. ` +
    `Edit ONLY ${planPath} — touch no code or other files. Return a short changelog (bullets) of what you changed.`,
    { label: `revise-r${round}`, phase: 'Review loop', model: 'opus', effort: 'high' },
  )
  log(`Round ${round} revision: ${String(changelog).slice(0, 300)}`)
}

const residualBlockers = lastFindings.filter((f) => f.severity === 'blocker')
const residualMajors = lastFindings.filter((f) => f.severity === 'major')
const exhausted = !converged && !stalled && round === maxRounds

// ESCALATION: any non-converged exit (stalled OR exhausted at maxRounds) needs a human.
const escalated = !converged
const escalationReason = converged
  ? null
  : stalled
    ? `stalled after ${round} round(s): a revision failed to reduce the blocker+major count (stuck)`
    : `did not converge within maxRounds=${maxRounds}: ${residualBlockers.length} blocker(s) + ${residualMajors.length} major(s) remain`
if (escalated) {
  log(`⚠️ ESCALATION — plan could NOT converge (${escalationReason}). ` +
      `Do NOT treat this plan as READY. A human must resolve the residual ` +
      `${residualBlockers.length} blocker(s) + ${residualMajors.length} major(s), or revise the approach.`)
}

phase('Finalize')

// FINALIZE STALENESS — D1 re-checked (Plan 200). The multi-round review loop can run
// long; if someone else moved the plan doc on origin/main or local main WHILE this
// loop was revising its own copy, the revisions below were made against a copy that
// is no longer the authoritative one. This is the last point the workflow can still
// catch it before recommending READY.
const staleAtFinalize = await agent(
  stalePlanPromptText('FINALIZE'),
  { label: 'stale-plan-finalize', phase: 'Finalize', model: 'sonnet', effort: 'low', schema: STALE_PLAN },
)
const planWentStale = isPlanStale(staleAtFinalize)
if (planWentStale) {
  log(`⚠️ ESCALATION at FINALIZE — ${staleEscalation(staleAtFinalize, 'FINALIZE')}. Do NOT flip this plan to ` +
      `READY as-is — re-pull the authoritative copy and re-fold these revisions onto it.`)
}

const final = await agent(
  `Read the now-revised plan at ${planPath} (repo ${repo}). Spot-check its citations against the code (Read/Grep). ` +
  `The review loop ended: converged=${converged}, stalled=${stalled}, exhausted=${exhausted}, ` +
  `residual blockers=${residualBlockers.length}, residual majors=${residualMajors.length}, ` +
  `rounds where the Codex pass failed=${codexFailedRounds}, plan went STALE during the review=${planWentStale}. ` +
  `Return: (1) 'summary' — a <=6-line summary of the converged design; ` +
  `(2) 'residualQuestions' — the genuine design forks a HUMAN must decide (the operator's grill-me); these are NOT defects` +
  (planWentStale ? ` — you MUST include that the plan doc went stale during this review and must be re-pulled + re-folded before READY` : ``) + `; ` +
  `(3) 'recommendation' — 'READY' only if there are no residual blockers/majors, the residual questions are the kind ` +
  `a human simply picks, AND the plan did NOT go stale during the review; else 'NOT-READY'.`,
  { label: 'finalize', phase: 'Finalize', model: 'sonnet', effort: 'medium', schema: FINAL },
)

// The recommendation may NOT be READY unless the loop actually converged (no residual
// blockers/majors on a COMPLETE panel) AND the plan copy stayed current throughout. A
// run that escalated — stalled, exhausted, ended a round with a failed reviewer, or hit
// finalize-time staleness — is NOT-READY regardless of what the finalize agent inferred.
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
  planWentStale,
  final,
}
