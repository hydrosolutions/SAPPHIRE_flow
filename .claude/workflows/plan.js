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

phase('Ground')
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
      `  codex exec --sandbox read-only --skip-git-repo-check "$(cat <scratch-file>)"\n` +
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
const final = await agent(
  `Read the now-revised plan at ${planPath} (repo ${repo}). Spot-check its citations against the code (Read/Grep). ` +
  `The review loop ended: converged=${converged}, stalled=${stalled}, exhausted=${exhausted}, ` +
  `residual blockers=${residualBlockers.length}, residual majors=${residualMajors.length}, ` +
  `rounds where the Codex pass failed=${codexFailedRounds}. ` +
  `Return: (1) 'summary' — a <=6-line summary of the converged design; ` +
  `(2) 'residualQuestions' — the genuine design forks a HUMAN must decide (the operator's grill-me); these are NOT defects; ` +
  `(3) 'recommendation' — 'READY' only if there are no residual blockers/majors AND the residual questions are the kind a human simply picks; else 'NOT-READY'.`,
  { label: 'finalize', phase: 'Finalize', model: 'sonnet', effort: 'medium', schema: FINAL },
)

// The recommendation may NOT be READY unless the loop actually converged (no residual
// blockers/majors on a COMPLETE panel). A run that escalated — stalled, exhausted, or
// ended a round with a failed reviewer — is NOT-READY regardless of what the finalize
// agent inferred from a possibly-partial finding set.
if (final && !converged && final.recommendation !== 'NOT-READY') {
  log(`Overriding finalize recommendation → NOT-READY (converged=false, escalated=${escalated}).`)
  final.recommendation = 'NOT-READY'
}

return {
  planPath,
  rounds: round,
  converged,
  stalled,
  exhausted,
  escalated,
  escalationReason,
  residualBlockerCount: residualBlockers.length,
  residualMajorCount: residualMajors.length,
  residualFindings: lastFindings,
  codexFailedRounds,
  final,
}
