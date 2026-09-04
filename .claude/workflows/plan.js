export const meta = {
  name: 'plan',
  description: 'Read-only plan review: one Claude+Codex pair, explicit owner dispositions, and at most one confirmation review.',
  phases: [
    { title: 'Preflight' },
    { title: 'Review' },
    { title: 'Finalize' },
  ],
}

let A = args || {}
if (typeof A === 'string') {
  try { A = JSON.parse(A) } catch (_error) { A = {} }
}

const planPath = A.planPath
const repo = A.repo || '.'
const mode = A.mode || 'review'
const riskClass = A.riskClass
const priorReview = A.priorReview || null
const ownerDispositions = Array.isArray(A.ownerDispositions) ? A.ownerDispositions : []
const additionalReview = A.additionalReview || null
const codexTimeoutMs = A.codexTimeoutMs || 600000
const MANIFEST_MAX_BYTES = 8192

if (!planPath) throw new Error('plan requires args.planPath')
if (!['review', 'confirm'].includes(mode)) throw new Error("plan mode must be 'review' or 'confirm'")
if (!['ordinary', 'high'].includes(riskClass)) {
  throw new Error("plan requires explicit riskClass 'ordinary' or 'high'")
}

const CONTEXT = {
  type: 'object',
  required: ['inspectionExitCode', 'inspectionRawOutput', 'headSha'],
  properties: {
    inspectionExitCode: { type: 'number' },
    inspectionRawOutput: { type: 'string', maxLength: MANIFEST_MAX_BYTES },
    headSha: { type: 'string' },
  },
}

const FINDING = {
  type: 'object',
  required: ['id', 'severity', 'location', 'violatedContract', 'correction'],
  properties: {
    id: { type: 'string', maxLength: 1000 },
    severity: { type: 'string', enum: ['blocker', 'major', 'minor'] },
    location: { type: 'string', maxLength: 1000 },
    violatedContract: { type: 'string', maxLength: 1000 },
    correction: { type: 'string', maxLength: 1000 },
  },
}

const REVIEW = {
  type: 'object',
  required: ['reviewerFailed', 'findings'],
  properties: {
    reviewerFailed: { type: 'boolean' },
    rawVerdict: { type: 'string', maxLength: 8000 },
    findings: { type: 'array', items: FINDING },
  },
}

const CODEX_REVIEW = {
  type: 'object',
  required: ['reviewerFailed', 'findings', 'exitCode', 'rawVerdict'],
  properties: {
    reviewerFailed: { type: 'boolean' },
    findings: { type: 'array', items: FINDING },
    exitCode: { type: 'number' },
    rawVerdict: { type: 'string', maxLength: 8000 },
  },
}

const STALENESS = {
  type: 'object',
  required: ['fetchOk', 'checkOk', 'staleRefs', 'behindCount'],
  properties: {
    fetchOk: { type: 'boolean' },
    checkOk: { type: 'boolean' },
    staleRefs: { type: 'array', items: { type: 'string', maxLength: 1000 } },
    behindCount: { type: 'number' },
    notes: { type: 'string', maxLength: 1000 },
  },
}

function stalenessPrompt(stage) {
  return (
    `In repo ${repo}, run the fail-closed ${stage} staleness check for ${planPath}. Do not edit files. ` +
    `Fetch origin/main first; fetch failure means fetchOk=false and checkOk=false. Report ` +
    `git rev-list --count HEAD..origin/main as behindCount (0 only when the command succeeds with zero). ` +
    `For each of origin/main and main, use cat-file to check whether the plan exists at the ref tip. ` +
    `If absent, use git log on that ref to distinguish a brand-new branch plan (no history: current) from ` +
    `a plan removed upstream (history exists while worktree file exists: stale). If present, find the latest ` +
    `plan-changing commit and test whether it is an ancestor of HEAD. When it is not, compare the actual ` +
    `worktree file with the ref; record the ref in staleRefs only when the contents differ. Any unexpected ` +
    `git exit or unverifiable comparison means checkOk=false. Return concise facts only, without command output.`
  )
}

function stale(check) {
  return !check || !check.fetchOk || !check.checkOk ||
    !Array.isArray(check.staleRefs) || check.staleRefs.length > 0
}

function utf8Bytes(text) {
  const bytes = []
  for (let index = 0; index < text.length; index += 1) {
    let codePoint = text.charCodeAt(index)
    if (codePoint >= 0xd800 && codePoint <= 0xdbff) {
      const next = index + 1 < text.length ? text.charCodeAt(index + 1) : 0
      if (next >= 0xdc00 && next <= 0xdfff) {
        codePoint = 0x10000 + ((codePoint - 0xd800) << 10) + next - 0xdc00
        index += 1
      } else {
        codePoint = 0xfffd
      }
    } else if (codePoint >= 0xdc00 && codePoint <= 0xdfff) {
      codePoint = 0xfffd
    }

    if (codePoint <= 0x7f) bytes.push(codePoint)
    else if (codePoint <= 0x7ff) {
      bytes.push(0xc0 | (codePoint >> 6), 0x80 | (codePoint & 0x3f))
    } else if (codePoint <= 0xffff) {
      bytes.push(0xe0 | (codePoint >> 12), 0x80 | ((codePoint >> 6) & 0x3f), 0x80 | (codePoint & 0x3f))
    } else {
      bytes.push(
        0xf0 | (codePoint >> 18),
        0x80 | ((codePoint >> 12) & 0x3f),
        0x80 | ((codePoint >> 6) & 0x3f),
        0x80 | (codePoint & 0x3f),
      )
    }
  }
  return bytes
}

function fingerprint(text) {
  let value = 0x811c9dc5
  for (const byte of utf8Bytes(String(text))) {
    value ^= byte
    value = Math.imul(value, 0x01000193) >>> 0
  }
  return value.toString(16).padStart(8, '0')
}

function validFingerprint(value) {
  return typeof value === 'string' && /^[0-9a-f]{8}$/.test(value)
}

function manifestValid(manifest) {
  const uniqueIds = (items) => Array.isArray(items) && items.length > 0 &&
    new Set(items.map((item) => item.id)).size === items.length
  return !!manifest && manifest.valid === true && Array.isArray(manifest.diagnostics) &&
    manifest.diagnostics.length === 0 && typeof manifest.status === 'string' &&
    validFingerprint(manifest.documentFingerprint) && uniqueIds(manifest.tasks) &&
    manifest.tasks.every((task) => typeof task.id === 'string' && task.id.trim().length > 0 &&
      ['N/A', 'executable'].includes(task.preChangeMode) &&
      validFingerprint(task.preChangeFingerprint) && validFingerprint(task.verificationFingerprint)) &&
    uniqueIds(manifest.exitGates) && manifest.exitGates.every((gate) =>
      typeof gate.id === 'string' && gate.id.trim().length > 0 && validFingerprint(gate.fingerprint),
    )
}

function parseManifest(context) {
  if (!context || context.inspectionExitCode !== 0 ||
      typeof context.inspectionRawOutput !== 'string' ||
      utf8Bytes(context.inspectionRawOutput).length > MANIFEST_MAX_BYTES) return null
  try {
    const manifest = JSON.parse(context.inspectionRawOutput)
    return manifestValid(manifest) ? manifest : null
  } catch (_error) {
    return null
  }
}

function usable(report) {
  if (!report || report.reviewerFailed !== false || !Array.isArray(report.findings)) return false
  const ids = report.findings.map((finding) => finding.id)
  return new Set(ids).size === ids.length && report.findings.every((finding) =>
    ['blocker', 'major', 'minor'].includes(finding.severity) &&
    [finding.id, finding.location, finding.violatedContract, finding.correction]
      .every((value) => typeof value === 'string' && value.trim().length > 0),
  )
}

function codexUsable(report) {
  return usable(report) && Number.isInteger(report.exitCode) && report.exitCode === 0 &&
    typeof report.rawVerdict === 'string' && report.rawVerdict.trim().length > 0
}

function priorCodexUsable(report) {
  return usable(report) && Number.isInteger(report.exitCode) && report.exitCode === 0 &&
    report.rawVerdictPresent === true
}

function publicReview(report) {
  if (!report) return null
  const result = { reviewerFailed: report.reviewerFailed, findings: report.findings }
  if (Number.isInteger(report.exitCode)) {
    result.exitCode = report.exitCode
    result.rawVerdictPresent = typeof report.rawVerdict === 'string' && report.rawVerdict.trim().length > 0
  }
  return result
}

function blocking(report) {
  if (!usable(report)) return []
  return report.findings.filter((finding) => ['blocker', 'major'].includes(finding.severity))
}

function sameUniqueStrings(actual, expected) {
  if (!Array.isArray(actual) || !Array.isArray(expected)) return false
  if (new Set(actual).size !== actual.length || new Set(expected).size !== expected.length) return false
  if (actual.length !== expected.length) return false
  const sortedActual = [...actual].sort()
  const sortedExpected = [...expected].sort()
  return sortedActual.every((value, index) => value === sortedExpected[index])
}

function requiredPriorFindings() {
  if (!priorReview || !priorReview.reviews) return []
  const reports = [priorReview.reviews.claude, priorReview.reviews.codex]
  if (riskClass === 'high' && priorReview.reviews.additional) reports.push(priorReview.reviews.additional)
  return reports.reduce((items, report) => items.concat(blocking(report)), [])
}

function dispositionsCover(findings) {
  const allowed = ['fix', 'reject', 'follow-up', 'accept-risk']
  const findingIds = findings.map((finding) => finding.id)
  const dispositionIds = ownerDispositions.map((item) => item.findingId)
  if (!sameUniqueStrings(dispositionIds, findingIds)) return false
  return findings.every((finding) => {
    const matches = ownerDispositions.filter((item) => item.findingId === finding.id)
    if (matches.length !== 1 || !allowed.includes(matches[0].disposition)) return false
    return matches[0].disposition === 'fix' || String(matches[0].rationale || '').trim().length > 0
  })
}

function sameFinding(left, right) {
  return left.id === right.id && left.location === right.location &&
    left.violatedContract === right.violatedContract
}

function acceptedPriorFindings() {
  if (mode !== 'confirm') return []
  const acceptedIds = new Set(ownerDispositions
    .filter((item) => item.disposition === 'accept-risk')
    .map((item) => item.findingId))
  return requiredPriorFindings().filter((finding) => acceptedIds.has(finding.id))
}

const acceptedRisks = acceptedPriorFindings()

function compactPriorReview() {
  if (!priorReview) return null
  return {
    planPath: priorReview.planPath,
    mode: priorReview.mode,
    riskClass: priorReview.riskClass,
    reviewedHead: priorReview.reviewedHead,
    planFingerprint: priorReview.planFingerprint,
    findings: {
      claude: priorReview.reviews?.claude?.findings || [],
      codex: priorReview.reviews?.codex?.findings || [],
      additional: priorReview.reviews?.additional?.findings || [],
    },
  }
}

phase('Preflight')

const staleAtStart = await agent(
  stalenessPrompt('PREFLIGHT'),
  { label: 'plan-staleness-preflight', phase: 'Preflight', model: 'sonnet', effort: 'low', schema: STALENESS },
)
if (stale(staleAtStart)) {
  return {
    planPath, mode, recommendation: 'NOT_READY', inputErrors: ['Plan staleness could not be cleared'],
    reviews: { claude: null, codex: null, additional: publicReview(additionalReview) }, acceptedRisks,
  }
}
if (staleAtStart.behindCount > 0) log(`Warning: branch is ${staleAtStart.behindCount} commit(s) behind origin/main.`)

const context = await agent(
  `In repo ${repo}, run exactly: uv run python scripts/check_readiness.py --inspect-json ${planPath}. ` +
  `Return its numeric exit code as inspectionExitCode and its unedited stdout as inspectionRawOutput. ` +
  `Also run git rev-parse HEAD and return that SHA. Do not return plan text, task contracts, summaries, ` +
  `or suggestions, and do not edit anything.`,
  { label: 'plan-context', phase: 'Preflight', model: 'sonnet', effort: 'low', schema: CONTEXT },
)

const inputErrors = []
const manifest = parseManifest(context)
if (!manifest || typeof context.headSha !== 'string' || !/^[0-9a-f]{7,40}$/i.test(context.headSha.trim())) {
  inputErrors.push('Context manifest or reviewed HEAD is invalid')
}
if (mode === 'confirm') {
  if (!manifest || !priorReview || !priorReview.planFingerprint || !priorReview.reviews) {
    inputErrors.push('Confirm mode requires the prior review packet')
  } else if (priorReview.mode !== 'review' || priorReview.planPath !== planPath ||
      priorReview.riskClass !== riskClass || !priorReview.reviewedHead ||
      !validFingerprint(priorReview.planFingerprint)) {
    inputErrors.push('Prior review packet does not match this plan confirmation')
  } else if (!usable(priorReview.reviews.claude) || !priorCodexUsable(priorReview.reviews.codex)) {
    inputErrors.push('Prior review packet does not contain both required reviewers')
  } else if (!dispositionsCover(requiredPriorFindings())) {
    inputErrors.push('Every prior blocker and major needs exactly one valid owner disposition')
  }
}
if (riskClass === 'high' && !usable(additionalReview)) {
  inputErrors.push('High-risk review requires a usable additional independent panel report')
}
if (inputErrors.length > 0) {
  return {
    planPath, mode, reviewedHead: context?.headSha || null,
    riskClass,
    planFingerprint: manifest?.documentFingerprint || null,
    recommendation: 'REVIEW_INCOMPLETE', inputErrors,
    reviews: { claude: null, codex: null, additional: publicReview(additionalReview) }, acceptedRisks,
  }
}

phase('Review')

const reviewContext = {
  planPath,
  reviewedHead: context.headSha,
  status: manifest.status,
  documentFingerprint: manifest.documentFingerprint,
  tasks: manifest.tasks,
  exitGates: manifest.exitGates,
}

const reviewScope = mode === 'review'
  ? `Review the complete plan against its stated scope and every task contract.`
  : `Review the complete current plan again as the single confirmation pass. Recheck prior findings and ` +
    `owner dispositions, but also report any blocker or major still present anywhere in the current plan.`
const priorContext = compactPriorReview()

const claudePrompt =
  `You are the Claude design and proportionality reviewer. Read ${planPath}, AGENTS.md, CLAUDE.md, ` +
  `docs/workflow.md, docs/v0-scope.md, and only standards directly named by the plan. ${reviewScope} ` +
  `Prefer the smallest correct ` +
  `scope; do not block on alternative architecture, speculative hardening, or desirable follow-ups. ` +
  `A blocker/major is allowed only for an unsafe, contradictory, unexecutable, stated-scope gap, or ` +
  `non-discriminating verification. Use IDs CLAUDE-001 onward (preserve a prior ID when it remains). ` +
  `Every finding must name the exact location, violated task/decision/repo rule, and smallest correction. ` +
  `Return findings only: no plan summary, duplicated task text, or process narrative. Do not edit files.\n\n` +
  `Context manifest:\n${JSON.stringify(reviewContext)}\n\n` +
  `Prior review:\n${JSON.stringify(priorContext)}\n\nOwner dispositions:\n${JSON.stringify(ownerDispositions)}`

const codexPrompt =
  `Independently review ${planPath} in ${repo}. ${reviewScope} Verify claims, callers, contracts, and each ` +
  `task's Pre-change and Verification against repository source. Cite exact file:line or plan section. ` +
  `Do not block on preferences or follow-ups. Use IDs CODEX-001 onward and preserve prior IDs that remain. ` +
  `For each finding output severity, location, violated contract, and smallest sufficient correction. ` +
  `Return findings only in under 6,000 characters: no plan summary, duplicated task text, or process ` +
  `narrative. Do not edit files.\n\n` +
  `Context manifest:\n${JSON.stringify(reviewContext)}\n\nPrior review:\n` +
  `${JSON.stringify(priorContext)}\n\nOwner dispositions:\n${JSON.stringify(ownerDispositions)}`

const promptFingerprint = fingerprint(codexPrompt)
const codexPromptPath = `sapphire-flow-plan-${mode}-${promptFingerprint}-codex-review.md`

const [claudeReview, codexReview] = await parallel([
  () => agent(
    claudePrompt,
    { label: `plan-claude-${mode}`, phase: 'Review', model: 'sonnet', effort: 'high', schema: REVIEW },
  ),
  () => agent(
    `You are a relay for a real Codex review. Use the Write tool to write only ` +
    `${codexPromptPath}, with the exact contents between the CODEX_REVIEW_PROMPT tags below (exclude the tags). ` +
    `Then, in one foreground Bash tool call, run exactly ./scripts/codex-review.sh ${codexPromptPath}, capture ` +
    `its numeric exit and raw output, and set that Bash call's timeout to ${codexTimeoutMs}ms. Do not use ` +
    `heredocs, redirects, background execution, or a hand-written codex exec command. ` +
    `Do not edit tracked repository files or any other file. On timeout, non-zero exit, empty ` +
    `output, or an unparseable verdict, retry once; then return reviewerFailed=true rather than a clean ` +
    `report. Always return the final numeric script exitCode. Otherwise transcribe Codex faithfully into ` +
    `the schema and include its unedited output as ` +
    `rawVerdict. Add no opinion. After the final attempt, replace the temporary prompt file contents with ` +
    `temporary prompt cleared.\n\n<CODEX_REVIEW_PROMPT>\n${codexPrompt}\n` +
    `</CODEX_REVIEW_PROMPT>`,
    { label: `plan-codex-${mode}`, phase: 'Review', model: 'sonnet', effort: 'low', schema: CODEX_REVIEW },
  ),
])

const reviewersComplete = usable(claudeReview) && codexUsable(codexReview)
const currentReports = [claudeReview, codexReview]
if (riskClass === 'high') currentReports.push(additionalReview)
const hasBlocking = currentReports.some((report) =>
  blocking(report).some((finding) => !acceptedRisks.some((accepted) => sameFinding(finding, accepted))),
)

phase('Finalize')

const staleAtEnd = await agent(
  stalenessPrompt('FINALIZE'),
  { label: 'plan-staleness-finalize', phase: 'Finalize', model: 'sonnet', effort: 'low', schema: STALENESS },
)
const planWentStale = stale(staleAtEnd)

let recommendation = 'REVIEW_INCOMPLETE'
let ownerAction = null
if (reviewersComplete) {
  recommendation = mode === 'review' || hasBlocking || planWentStale ? 'NOT_READY' : 'READY'
  if (mode === 'review' && !planWentStale) {
    ownerAction = hasBlocking ? 'OWNER_DISPOSITION_REQUIRED' : 'CONFIRM_REQUIRED'
  }
}

return {
  planPath,
  mode,
  riskClass,
  reviewedHead: context.headSha,
  planFingerprint: manifest.documentFingerprint,
  reviews: {
    claude: publicReview(claudeReview),
    codex: publicReview(codexReview),
    additional: publicReview(additionalReview),
  },
  ownerDispositions,
  acceptedRisks,
  planWentStale,
  ownerAction,
  recommendation,
}
