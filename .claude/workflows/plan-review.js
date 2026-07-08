export const meta = {
  name: 'plan-review',
  description: 'Iteratively review + improve a DRAFT design-plan doc via an adversarial, code-grounded planner↔reviewer loop. Converges when NO blockers AND NO majors remain (or stops on no-progress / maxRounds), then surfaces the residual design forks a human must decide (the grill-me). Does NOT implement — it only edits the plan doc in place. hold-at-PR: the caller owns the branch/PR.',
  phases: [
    { title: 'Ground' },
    { title: 'Review loop' },
    { title: 'Finalize' },
  ],
}

// ── USAGE ────────────────────────────────────────────────────────────────────
// Workflow({ name: 'plan-review', args: { planPath: 'docs/plans/NNN-....md', repo: '/abs/repo/path', maxRounds: 3 } })
//   planPath  (required)  the DRAFT plan doc to review + improve — EDITED IN PLACE.
//   repo      (optional)  repo root (default '.').
//   maxRounds (optional)  max review↔revise rounds (default 3).
// Returns: { converged, stalled, exhausted, residualBlockerCount, residualMajorCount,
//            residualFindings, final:{ summary, residualQuestions, recommendation } }.
//
// WHAT IT DOES: an adversarial, code-grounded planner↔reviewer loop — 4 diverse
//   reviewers (design / feasibility / completeness / proportionality) critique the plan against the
//   REAL code each round (they cite file:line); a planner revises the doc; converges
//   on no blockers+majors, then surfaces the residual design forks a HUMAN must
//   decide (the grill-me). It is the useful half of vision-build/WF2 (planner↔reviewer)
//   without the implementation gates that can block locally.
//
// hold-at-PR: it mutates ONLY the plan doc; the CALLER owns the branch/PR. After it
//   returns, review the plan diff + the residual grill-me questions, settle those,
//   flip the plan Status to READY, then implement SEPARATELY (e.g. direct-finish agents).
//   Run it on a branch that has the plan doc + the code in the working tree.
//
// Built + dogfooded 2026-07-03 (2 self-review rounds caught 5 majors + an
// args-serialization bug); first used on Plan 093 (converged in 2 rounds → READY).

// ── args: { planPath (required), repo?, maxRounds? } ─────────────────────────
// Robust to `args` arriving as a JSON string (Workflow-tool serialization) or object.
let A = args || {}
if (typeof A === 'string') {
  try { A = JSON.parse(A) } catch (_e) { A = {} }
}
const planPath = A.planPath
const repo = A.repo || '.'
const maxRounds = A.maxRounds || 3
if (!planPath) {
  throw new Error('plan-review requires args.planPath (the DRAFT plan doc to review + improve)')
}

// A reviewer's findings: only GENUINE problems, each grounded + actionable.
// (No `verdict` field — it is derived from findings.length, so it can't contradict the data.)
const FINDINGS = {
  type: 'object',
  required: ['findings'],
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['severity', 'issue', 'suggestion'],
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

// Four adversarial lenses — perspective-diverse, all code-grounded.
// NOTE: completeness and proportionality are deliberate opposites — missing coverage
// vs. present-but-unnecessary detail/scope. Keep both: the tension is what stops the
// additive loop from drifting into over-engineering.
const LENSES = [
  'DESIGN SOUNDNESS — is the proposed approach correct, and is it the SIMPLEST correct approach? Name a better alternative if the plan ignores one. Flag internal contradictions.',
  'FEASIBILITY + CODE-GROUNDING — will this actually work against the REAL codebase? Verify every cited file:line / symbol / behavior with Read+Grep; flag any claim that assumes behavior the code does not have, or a citation that is stale/wrong.',
  'COMPLETENESS — what does the plan MISS? adjacent code paths, other callers, tests, config, migrations, the ForecastInterface contract, failure modes, backward-compat. What would break if implemented as written?',
  'PROPORTIONALITY (guard against over-engineering) — is the proposed solution more complex than the problem requires? Flag over-scope, gold-plating, speculative generality, unnecessary phases/abstractions, and REFERENCE detail that belongs in code/docstrings rather than the doc (it will rot there). Judge detail against what the artifact is FOR — not "is anything missing" (that is the completeness lens). What can be cut or simplified WITHOUT losing what the artifact must deliver? Propose specific cuts. Return an empty findings array if it is already lean.',
]

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
while (round < maxRounds) {
  round += 1

  // Diverse reviewers in parallel — each re-reads the CURRENT (possibly already-revised) plan.
  const reviews = (await parallel(
    LENSES.map((lens, i) => () =>
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
  )).filter(Boolean)

  // A dropped reviewer (null result) would hide its lens's findings and risk a
  // FALSE convergence — never declare "clean" on a partial panel.
  const lost = LENSES.length - reviews.length
  if (lost > 0) log(`Round ${round}: WARNING — ${lost} reviewer(s) returned no result.`)

  const findings = reviews.flatMap((r) => r.findings || [])
  const blockers = findings.filter((f) => f.severity === 'blocker')
  const majors = findings.filter((f) => f.severity === 'major')
  const open = blockers.length + majors.length
  lastFindings = findings
  log(`Round ${round}: ${blockers.length} blocker(s), ${majors.length} major(s), ${findings.length} finding(s)`)

  if (open === 0) {
    if (lost === 0) {
      converged = true
      log(`Round ${round}: no blockers or majors — converged.`)
      break
    }
    // 0 findings but the panel was incomplete → re-review next round, don't converge.
    log(`Round ${round}: 0 findings but ${lost} reviewer(s) failed — re-reviewing, not converging.`)
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
    `Reviewers raised these findings:\n${JSON.stringify(findings, null, 2)}\n\n` +
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

phase('Finalize')
const final = await agent(
  `Read the now-revised plan at ${planPath} (repo ${repo}). Spot-check its citations against the code (Read/Grep). ` +
  `The review loop ended: converged=${converged}, stalled=${stalled}, exhausted=${exhausted}, ` +
  `residual blockers=${residualBlockers.length}, residual majors=${residualMajors.length}. ` +
  `Return: (1) 'summary' — a <=6-line summary of the converged design; ` +
  `(2) 'residualQuestions' — the genuine design forks a HUMAN must decide (the operator's grill-me); these are NOT defects; ` +
  `(3) 'recommendation' — 'READY' only if there are no residual blockers/majors AND the residual questions are the kind a human simply picks; else 'NOT-READY'.`,
  { label: 'finalize', phase: 'Finalize', model: 'sonnet', effort: 'medium', schema: FINAL },
)

return {
  planPath,
  rounds: round,
  converged,
  stalled,
  exhausted,
  residualBlockerCount: residualBlockers.length,
  residualMajorCount: residualMajors.length,
  residualFindings: lastFindings,
  final,
}
