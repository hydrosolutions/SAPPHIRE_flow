export const meta = {
  name: 'implement',
  description: 'Implement every READY-plan task with evidence, verify once, review once, and allow one owner-scoped confirmation repair.',
  phases: [
    { title: 'Preflight' },
    { title: 'Implement' },
    { title: 'Verify' },
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
const baseBranch = A.baseBranch || 'main'
const mode = A.mode || 'build'
const riskClass = A.riskClass
const priorReview = A.priorReview || null
const ownerDispositions = Array.isArray(A.ownerDispositions) ? A.ownerDispositions : []
const additionalReview = A.additionalReview || null
const codexTimeoutMs = A.codexTimeoutMs || 600000
const MANIFEST_MAX_BYTES = 8192

if (!planPath) throw new Error('implement requires args.planPath')
if (!['build', 'confirm'].includes(mode)) throw new Error("implement mode must be 'build' or 'confirm'")
if (!['ordinary', 'high'].includes(riskClass)) {
  throw new Error("implement requires explicit riskClass 'ordinary' or 'high'")
}

const asSha = (value) => {
  const normalized = typeof value === 'string' ? value.trim().toLowerCase() : ''
  return /^[0-9a-f]{7,40}$/.test(normalized) ? normalized : null
}

const tagStateValid = (value) => Number.isInteger(value?.tagCount) && value.tagCount >= 0 &&
  typeof value.tagFingerprint === 'string' &&
  /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/.test(value.tagFingerprint.trim().toLowerCase())

const READINESS = {
  type: 'object',
  required: ['exitCode', 'outputExcerpt'],
  properties: {
    exitCode: { type: 'number' },
    outputExcerpt: { type: 'string', maxLength: 1000 },
  },
}

const CONTEXT = {
  type: 'object',
  required: [
    'inspectionExitCode', 'inspectionRawOutput', 'headSha', 'worktreeStatus', 'branchName',
    'tagCount', 'tagFingerprint', 'remoteBranchSha',
  ],
  properties: {
    inspectionExitCode: { type: 'number' },
    inspectionRawOutput: { type: 'string', maxLength: MANIFEST_MAX_BYTES },
    headSha: { type: 'string' },
    worktreeStatus: { type: 'string', maxLength: 1000 },
    branchName: { type: 'string' },
    tagCount: { type: 'number' },
    tagFingerprint: { type: 'string', maxLength: 64 },
    remoteBranchSha: { type: 'string' },
  },
}

const TASK_EVIDENCE = {
  type: 'object',
  required: [
    'taskId', 'preChangeFingerprint', 'preChangeStatus', 'preChangeExitCode',
    'preChangeOutputExcerpt', 'verificationFingerprint', 'exitCode', 'outputExcerpt',
    'beforeEvidence', 'afterEvidence', 'status',
  ],
  properties: {
    taskId: { type: 'string' },
    preChangeFingerprint: { type: 'string' },
    preChangeStatus: {
      type: 'string',
      enum: ['EXPECTED_FAILURE', 'NOT_APPLICABLE', 'UNVERIFIABLE'],
    },
    preChangeExitCode: { type: 'number' },
    preChangeOutputExcerpt: { type: 'string', maxLength: 1000 },
    verificationFingerprint: { type: 'string' },
    exitCode: { type: 'number' },
    outputExcerpt: { type: 'string', maxLength: 1000 },
    beforeEvidence: { type: 'string', maxLength: 1000 },
    afterEvidence: { type: 'string', maxLength: 1000 },
    status: {
      type: 'string',
      enum: ['READY_FOR_VERIFICATION', 'PASS', 'FAIL', 'UNVERIFIABLE', 'PLAN_INCOMPLETE'],
    },
  },
}

const VERIFICATION_EVIDENCE = {
  type: 'object',
  required: ['taskId', 'verificationFingerprint', 'exitCode', 'outputExcerpt', 'afterEvidence', 'status'],
  properties: {
    taskId: { type: 'string' },
    verificationFingerprint: { type: 'string' },
    exitCode: { type: 'number' },
    outputExcerpt: { type: 'string', maxLength: 1000 },
    afterEvidence: { type: 'string', maxLength: 1000 },
    status: { type: 'string', enum: ['PASS', 'FAIL', 'UNVERIFIABLE'] },
  },
}

const GATE_EVIDENCE = {
  type: 'object',
  required: ['gateId', 'fingerprint', 'exitCode', 'outputExcerpt'],
  properties: {
    gateId: { type: 'string' },
    fingerprint: { type: 'string' },
    exitCode: { type: 'number' },
    outputExcerpt: { type: 'string', maxLength: 1000 },
  },
}

const CHANGE_REPORT = {
  type: 'object',
  required: ['taskEvidence', 'changedFiles', 'deviations', 'residualRisks', 'rootCauseBundles', 'committed'],
  properties: {
    taskEvidence: { type: 'array', items: TASK_EVIDENCE },
    changedFiles: { type: 'array', items: { type: 'string' } },
    deviations: { type: 'array', items: { type: 'string', maxLength: 1000 } },
    residualRisks: { type: 'array', items: { type: 'string', maxLength: 1000 } },
    rootCauseBundles: { type: 'array', items: { type: 'string', maxLength: 1000 } },
    committed: { type: 'boolean' },
    commitSha: { type: 'string' },
  },
}

const VERIFY = {
  type: 'object',
  required: [
    'headSha', 'branchName', 'tagCount', 'tagFingerprint', 'remoteBranchSha', 'diffNonEmpty',
    'worktreeStatus', 'versionBumped', 'scopeStatus', 'taskEvidence',
    'changedFiles', 'gateEvidence', 'deviations', 'planDiffOutputExcerpt', 'planDiffExitCode',
    'readinessExitCode', 'readinessOutputExcerpt',
  ],
  properties: {
    headSha: { type: 'string' },
    branchName: { type: 'string' },
    tagCount: { type: 'number' },
    tagFingerprint: { type: 'string', maxLength: 64 },
    remoteBranchSha: { type: 'string' },
    diffNonEmpty: { type: 'boolean' },
    worktreeStatus: { type: 'string', maxLength: 1000 },
    versionBumped: { type: 'boolean' },
    scopeStatus: { type: 'string', enum: ['IN_SCOPE', 'DEVIATION'] },
    taskEvidence: { type: 'array', items: VERIFICATION_EVIDENCE },
    changedFiles: { type: 'array', items: { type: 'string' } },
    gateEvidence: { type: 'array', items: GATE_EVIDENCE },
    deviations: { type: 'array', items: { type: 'string', maxLength: 1000 } },
    planDiffOutputExcerpt: { type: 'string', maxLength: 1000 },
    planDiffExitCode: { type: 'number' },
    readinessExitCode: { type: 'number' },
    readinessOutputExcerpt: { type: 'string', maxLength: 1000 },
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
    `git rev-list --count HEAD..origin/main as behindCount. For origin/main and main, use cat-file to ` +
    `check whether the plan exists at the ref tip. When absent, use git log to distinguish a new branch ` +
    `plan with no ref history from a plan removed upstream. When present, find the latest plan-changing ` +
    `commit and test whether it is an ancestor of HEAD; if not, compare the actual worktree file with the ` +
    `ref. Record only differing or removed refs in staleRefs. Any unexpected git exit or unverifiable ` +
    `comparison means checkOk=false. Return concise facts only, without command output.`
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

function manifestValid(manifest) {
  const validFingerprint = (value) => typeof value === 'string' && /^[0-9a-f]{8}$/.test(value)
  const uniqueIds = (items) => Array.isArray(items) && items.length > 0 &&
    new Set(items.map((item) => item.id)).size === items.length
  return !!manifest && manifest.valid === true && manifest.status === 'READY' &&
    Array.isArray(manifest.diagnostics) && manifest.diagnostics.length === 0 &&
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

function taskIdsMatch(evidence, tasks) {
  if (!Array.isArray(evidence) || evidence.length !== tasks.length) return false
  const expected = tasks.map((task) => task.id).sort()
  const actual = evidence.map((item) => item.taskId).sort()
  return expected.every((taskId, index) => taskId === actual[index])
}

function sameUniqueStrings(actual, expected) {
  if (!Array.isArray(actual) || !Array.isArray(expected)) return false
  if (new Set(actual).size !== actual.length || new Set(expected).size !== expected.length) return false
  if (actual.length !== expected.length) return false
  const sortedActual = [...actual].sort()
  const sortedExpected = [...expected].sort()
  return sortedActual.every((value, index) => value === sortedExpected[index])
}

function implementationEvidenceHasContent(evidence) {
  return typeof evidence.beforeEvidence === 'string' && evidence.beforeEvidence.trim().length > 0 &&
    typeof evidence.afterEvidence === 'string' && evidence.afterEvidence.trim().length > 0 &&
    typeof evidence.outputExcerpt === 'string' && evidence.outputExcerpt.trim().length > 0
}

function verificationEvidenceHasContent(evidence) {
  return typeof evidence.afterEvidence === 'string' && evidence.afterEvidence.trim().length > 0 &&
    typeof evidence.outputExcerpt === 'string' && evidence.outputExcerpt.trim().length > 0
}

function preChangeEvidenceValid(evidence, task) {
  if (!task || evidence.preChangeFingerprint !== task.preChangeFingerprint ||
      !Number.isInteger(evidence.preChangeExitCode) ||
      typeof evidence.preChangeOutputExcerpt !== 'string' ||
      evidence.preChangeOutputExcerpt.trim().length === 0) {
    return false
  }
  return task.preChangeMode === 'N/A'
    ? evidence.preChangeStatus === 'NOT_APPLICABLE' && evidence.preChangeExitCode === 0
    : evidence.preChangeStatus === 'EXPECTED_FAILURE' && evidence.preChangeExitCode !== 0
}

function taskEvidenceComplete(evidence, tasks) {
  const task = tasks.find((item) => item.id === evidence.taskId)
  return !!task && evidence.verificationFingerprint === task.verificationFingerprint &&
    Number.isInteger(evidence.exitCode) && evidence.exitCode === 0 &&
    evidence.status === 'PASS' && verificationEvidenceHasContent(evidence)
}

function taskEvidenceReady(evidence, tasks) {
  const task = tasks.find((item) => item.id === evidence.taskId)
  return !!task && preChangeEvidenceValid(evidence, task) &&
    evidence.verificationFingerprint === task.verificationFingerprint &&
    Number.isInteger(evidence.exitCode) && evidence.exitCode === 0 &&
    evidence.status === 'READY_FOR_VERIFICATION' && implementationEvidenceHasContent(evidence)
}

function gateEvidenceComplete(evidence, gates) {
  return taskIdsMatch(
    evidence?.map((item) => ({ taskId: item.gateId })) || [],
    gates.map((gate) => ({ id: gate.id })),
  ) && evidence.every((item) => {
    const gate = gates.find((candidate) => candidate.id === item.gateId)
    return !!gate && item.fingerprint === gate.fingerprint &&
      Number.isInteger(item.exitCode) && item.exitCode === 0 &&
      typeof item.outputExcerpt === 'string' && item.outputExcerpt.trim().length > 0
  })
}

function priorBlockingFindings() {
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
  return priorBlockingFindings().filter((finding) => acceptedIds.has(finding.id))
}

function compactPriorReview() {
  if (!priorReview) return null
  return {
    planPath: priorReview.planPath,
    mode: priorReview.mode,
    riskClass: priorReview.riskClass,
    baseBranch: priorReview.baseBranch,
    reviewedCommit: priorReview.reviewedCommit,
    planFingerprint: priorReview.planFingerprint,
    findings: {
      claude: priorReview.reviews?.claude?.findings || [],
      codex: priorReview.reviews?.codex?.findings || [],
      additional: priorReview.reviews?.additional?.findings || [],
    },
  }
}

function canonicalTaskEvidence(report, implementationEvidence) {
  return report.taskEvidence.map((verified) => {
    const implemented = implementationEvidence.find((item) => item.taskId === verified.taskId) || {}
    return {
      taskId: verified.taskId,
      preChangeFingerprint: implemented.preChangeFingerprint,
      preChangeStatus: implemented.preChangeStatus,
      preChangeExitCode: implemented.preChangeExitCode,
      preChangeOutputExcerpt: implemented.preChangeOutputExcerpt,
      verificationFingerprint: verified.verificationFingerprint,
      exitCode: verified.exitCode,
      outputExcerpt: verified.outputExcerpt,
      beforeEvidence: implemented.beforeEvidence,
      afterEvidence: verified.afterEvidence,
      status: verified.status,
    }
  })
}

function compactVerification(report, taskEvidence) {
  return {
    headSha: report.headSha,
    changedFiles: report.changedFiles,
    scopeStatus: report.scopeStatus,
    deviations: report.deviations,
    taskEvidence,
    gateEvidence: report.gateEvidence.map((item) => ({
      gateId: item.gateId,
      fingerprint: item.fingerprint,
      exitCode: item.exitCode,
      outputExcerpt: item.outputExcerpt,
    })),
  }
}

function stopResult(reason, taskEvidence = [], taskCompletion = 'INCOMPLETE') {
  return {
    planPath,
    mode,
    riskClass,
    baseBranch,
    taskCompletion,
    engineeringReview: 'INCOMPLETE',
    recommendation: 'NOT_READY',
    reason,
    taskEvidence,
    reviews: { claude: null, codex: null, additional: publicReview(additionalReview) },
    deviations: { change: [], verification: [] },
    acceptedRisks: acceptedPriorFindings(),
  }
}

phase('Preflight')

const readiness = await agent(
  `In repo ${repo}, run exactly: uv run python scripts/check_readiness.py ${planPath}. Do not edit anything. ` +
  `Return the numeric process exit code and a relevant output excerpt of at most 1,000 characters without ` +
  `interpreting readiness.`,
  { label: 'readiness-check', phase: 'Preflight', model: 'sonnet', effort: 'low', schema: READINESS },
)
const readinessOk = !!readiness && Number.isInteger(readiness.exitCode) &&
  typeof readiness.outputExcerpt === 'string' && readiness.outputExcerpt.length > 0 && readiness.exitCode === 0
if (!readinessOk) return stopResult('PLAN_INCOMPLETE: deterministic YAML READY check failed')

const staleAtStart = await agent(
  stalenessPrompt('PREFLIGHT'),
  { label: 'implement-staleness-preflight', phase: 'Preflight', model: 'sonnet', effort: 'low', schema: STALENESS },
)
if (stale(staleAtStart)) return stopResult('PLAN_INCOMPLETE: plan staleness could not be cleared')
if (staleAtStart.behindCount > 0) log(`Warning: branch is ${staleAtStart.behindCount} commit(s) behind origin/main.`)

const context = await agent(
  `In repo ${repo}, run exactly: uv run python scripts/check_readiness.py --inspect-json ${planPath}. ` +
  `Return its numeric exit code as inspectionExitCode and its unedited stdout as inspectionRawOutput. Also ` +
  `return the current HEAD SHA, branch name, tagCount, and tagFingerprint. Derive the fingerprint from ` +
  `git tag --list | LC_ALL=C sort | git hash-object --stdin and return only its hexadecimal hash, never tag ` +
  `names. Also return git status --porcelain (at most 1,000 characters), ` +
  `and the current origin/<branch> SHA or the literal NONE when that remote branch does not exist. Do not ` +
  `return plan text, task contracts, commands, or summaries. Do not review or edit anything.`,
  { label: 'implementation-context', phase: 'Preflight', model: 'sonnet', effort: 'low', schema: CONTEXT },
)

const manifest = parseManifest(context)
if (!manifest || !asSha(context.headSha)) return stopResult('PLAN_INCOMPLETE: context manifest is invalid')
const tasks = manifest.tasks
const exitGates = manifest.exitGates
if (context.worktreeStatus.trim().length > 0) {
  return stopResult('IMPLEMENTATION_INCOMPLETE: worktree must be clean before build or confirmation')
}
const branchAtStart = String(context.branchName || '').trim()
const branchSafeAtStart = branchAtStart.length > 0 && branchAtStart !== 'HEAD' &&
  branchAtStart !== 'main' && branchAtStart !== baseBranch &&
  tagStateValid(context) &&
  (context.remoteBranchSha === 'NONE' || !!asSha(context.remoteBranchSha))
if (!branchSafeAtStart) {
  return stopResult('IMPLEMENTATION_INCOMPLETE: build requires a named feature branch with a git baseline')
}

const preHead = asSha(context.headSha)
let fixFindings = []
if (mode === 'confirm') {
  if (!priorReview || !priorReview.reviews || !asSha(priorReview.reviewedCommit)) {
    return stopResult('NOT_READY: confirm requires the prior review packet and reviewed commit')
  }
  if (priorReview.mode !== 'build' || priorReview.planPath !== planPath ||
      priorReview.baseBranch !== baseBranch || priorReview.riskClass !== riskClass ||
      asSha(priorReview.reviewedCommit) !== preHead ||
      priorReview.planFingerprint !== manifest.documentFingerprint) {
    return stopResult('NOT_READY: reviewed commit or READY plan changed before confirmation')
  }
  const previousFindings = priorBlockingFindings()
  const priorTaskEvidence = priorReview.taskEvidence
  const priorImplementationComplete = taskIdsMatch(priorTaskEvidence, tasks) &&
    priorTaskEvidence.every((evidence) =>
      preChangeEvidenceValid(evidence, tasks.find((task) => task.id === evidence.taskId)) &&
      taskEvidenceComplete(evidence, tasks),
    )
  if (!usable(priorReview.reviews.claude) || !priorCodexUsable(priorReview.reviews.codex) ||
      !priorImplementationComplete || !dispositionsCover(previousFindings)) {
    return stopResult('NOT_READY: complete prior reports and one owner disposition per blocker/major are required')
  }
  const fixIds = new Set(ownerDispositions
    .filter((item) => item.disposition === 'fix')
    .map((item) => item.findingId))
  fixFindings = previousFindings.filter((finding) => fixIds.has(finding.id))
}

phase('Implement')

let implementerReport = null
let fixerReport = null

if (mode === 'build') {
  implementerReport = await agent(
    `You are the single Sonnet implementer for the READY plan ${planPath} in ${repo}. Read the plan directly. ` +
    `Implement every task ` +
    `in dependency order; do not split work across coding agents. Before changing a behavioral task, run its ` +
    `declared Pre-change/Verification evidence. If it is already green for behavior claimed missing, or cannot ` +
    `discriminate the change, stop without inventing a substitute and mark that task PLAN_INCOMPLETE. For each ` +
    `task return its manifest Pre-change and Verification fingerprints, preChangeStatus, numeric ` +
    `preChangeExitCode, numeric after-test exitCode, separate beforeEvidence and afterEvidence, and one ` +
    `relevant outputExcerpt per command capped at 1,000 characters. Never return full command output. ` +
    `Use (no output) as the excerpt for a successful silent command. ` +
    `A behavioral task requires EXPECTED_FAILURE and a non-zero pre-change exit; if it is already green, use ` +
    `UNVERIFIABLE and PLAN_INCOMPLETE. Documentation/mechanical tasks use NOT_APPLICABLE, exit 0, and their ` +
    `declared N/A reason; stop if the plan uses N/A for behavior-changing work. Run only task-targeted checks while editing. ` +
    `Do not run the plan's complete Exit gates; the independent verifier owns one complete exit-gate run per committed candidate. ` +
    `When related failures share a cause, report one root-cause bundle with task IDs, evidence, cause, and the ` +
    `smallest in-scope correction. Update affected docs. At a stable candidate run ` +
    `uv run bump-my-version bump patch, stage the version files with the patch, and commit conventionally on ` +
    `the current feature branch. Never tag, push, open a PR, merge, deploy, or adopt a scientific result. ` +
    `Return one concise taskEvidence entry for every task ID, without repeating plan text.\n\n` +
    `Task identity manifest:\n${JSON.stringify(tasks)}`,
    { label: 'implement-build', phase: 'Implement', model: 'sonnet', effort: 'high', schema: CHANGE_REPORT },
  )
} else if (mode === 'confirm' && fixFindings.length > 0) {
  fixerReport = await agent(
    `You are the only confirmation fixer for ${planPath} in ${repo}. Address only the owner-disposed fix ` +
    `findings below and any direct regression in the same touched area. Do not fix minors, follow-ups, rejected ` +
    `findings, or accepted risks. Use one root-cause bundle for related failures. Run affected task-targeted ` +
    `checks only; do not run the complete Exit gates. Read the plan directly and return fingerprint-bound ` +
    `Pre-change and Verification evidence for every affected task, with command excerpts capped at 1,000 ` +
    `characters and no repeated plan text. If the ` +
    `patch changes, run uv run bump-my-version bump patch, stage the version files, and commit conventionally. ` +
    `Never tag, push, open a PR, merge, deploy, or make a second repair pass.\n\nFix findings:\n` +
    `${JSON.stringify(fixFindings)}\n\nOwner dispositions:\n${JSON.stringify(ownerDispositions)}\n\n` +
    `Task identity manifest:\n${JSON.stringify(tasks)}`,
    { label: 'implement-confirm-fix', phase: 'Implement', model: 'sonnet', effort: 'high', schema: CHANGE_REPORT },
  )
}

if (mode === 'build') {
  const implementationAccounted = implementerReport && taskIdsMatch(implementerReport.taskEvidence, tasks)
  const implementationStopped = !implementationAccounted || implementerReport.taskEvidence.some(
    (evidence) => !taskEvidenceReady(evidence, tasks),
  )
  if (implementationStopped || !implementerReport.committed || !asSha(implementerReport.commitSha)) {
    return stopResult(
      'IMPLEMENTATION_INCOMPLETE: implementer did not produce one committed candidate with evidence for every task',
      implementerReport?.taskEvidence || [],
    )
  }
}
if (fixFindings.length > 0 &&
    (!fixerReport || !fixerReport.committed || !asSha(fixerReport.commitSha))) {
  return stopResult('NOT_READY: required confirmation repair did not produce a fresh commit', fixerReport?.taskEvidence || [])
}

phase('Verify')

const verifier = await agent(
  `You are the independent read-only verifier for the committed candidate of ${planPath} in ${repo}. Do not ` +
  `trust the implementer/fixer report and do not edit files. Read the READY plan and ` +
  `git diff ${baseBranch}...HEAD. Return current HEAD, branch name, tagCount, and tagFingerprint derived from ` +
  `git tag --list | LC_ALL=C sort | git hash-object --stdin; return only the hexadecimal hash, never tag ` +
  `names. Return the current origin/<branch> ` +
  `SHA or NONE, whether the base diff is non-empty, git status --porcelain capped at 1,000 characters, whether the candidate commit ` +
  `contains the required patch-version bump, changed files, scope ` +
  `status, and deviations. Run git diff ${preHead}...HEAD -- ${planPath}; return its numeric exit as ` +
  `planDiffExitCode and an output excerpt as planDiffOutputExcerpt, ` +
  `using an empty string only when that diff has no output. ` +
  `then rerun uv run python scripts/check_readiness.py ${planPath} and return its numeric exit and an ` +
  `output excerpt. Cap every command excerpt at 1,000 characters; never return full command output. ` +
  `Use (no output) for successful silent task or gate commands. ` +
  `Independently run every task's exact Verification and return exactly one PASS, ` +
  `FAIL, or UNVERIFIABLE entry per task ID with its manifest Verification fingerprint, numeric exit code, ` +
  `one outputExcerpt, and ` +
  `observed afterEvidence. ` +
  `Run every plan Exit gates command exactly ` +
  `once for this candidate; de-duplicate identical task/gate commands while mapping the same result to ` +
  `each task. Return one entry per gate ID with its manifest fingerprint, numeric exit code, and one ` +
  `outputExcerpt. This is the one complete exit-gate run per committed candidate. Read commands from the ` +
  `plan; do not repeat them in the response.\n\nTask identity manifest:\n${JSON.stringify(tasks)}\n\n` +
  `Exit-gate identity manifest:\n${JSON.stringify(exitGates)}`,
  { label: `verify-${mode}`, phase: 'Verify', model: 'sonnet', effort: 'medium', schema: VERIFY },
)

const verifiedHead = asSha(verifier?.headSha)
const repairRequired = mode === 'confirm' && fixFindings.length > 0
const candidateFresh = mode === 'build'
  ? !!verifiedHead && verifiedHead !== preHead
  : repairRequired
    ? !!verifiedHead && verifiedHead !== asSha(priorReview.reviewedCommit)
    : !!verifiedHead && verifiedHead === asSha(priorReview.reviewedCommit)
const reportCommit = mode === 'build' ? asSha(implementerReport?.commitSha) : asSha(fixerReport?.commitSha)
const reportMatches = !repairRequired && mode === 'confirm' ? true : reportCommit === verifiedHead
const taskIdsVerified = !!verifier && taskIdsMatch(verifier.taskEvidence, tasks)
const hasUnverifiableTask = taskIdsVerified && verifier.taskEvidence.some((item) => item.status === 'UNVERIFIABLE')
const tasksComplete = taskIdsVerified && verifier.taskEvidence.every(
  (item) => taskEvidenceComplete(item, tasks),
)
const taskCompletion = tasksComplete ? 'COMPLETE' : hasUnverifiableTask ? 'UNVERIFIABLE' : 'INCOMPLETE'
const gatesPassed = !!verifier && gateEvidenceComplete(verifier.gateEvidence, exitGates)
const branchSafe = !!verifier && verifier.branchName === branchAtStart &&
  tagStateValid(verifier) && verifier.tagCount === context.tagCount &&
  verifier.tagFingerprint === context.tagFingerprint &&
  verifier.remoteBranchSha === context.remoteBranchSha
const planUnchanged = !!verifier && Number.isInteger(verifier.planDiffExitCode) &&
  verifier.planDiffExitCode === 0 && verifier.planDiffOutputExcerpt.trim().length === 0
const finalReadinessOk = !!verifier && Number.isInteger(verifier.readinessExitCode) &&
  verifier.readinessExitCode === 0 && typeof verifier.readinessOutputExcerpt === 'string' &&
  verifier.readinessOutputExcerpt.trim().length > 0
const implementationEvidence = mode === 'build'
  ? implementerReport.taskEvidence
  : priorReview.taskEvidence || []
const taskEvidence = verifier ? canonicalTaskEvidence(verifier, implementationEvidence) : []
const verificationPassed = !!verifier && candidateFresh && reportMatches && verifier.diffNonEmpty &&
  verifier.worktreeStatus.trim().length === 0 && verifier.versionBumped &&
  verifier.scopeStatus === 'IN_SCOPE' && gatesPassed && tasksComplete && branchSafe &&
  planUnchanged && finalReadinessOk

if (!verificationPassed) {
  return {
    ...stopResult('IMPLEMENTATION_INCOMPLETE: independent task or repository verification failed', taskEvidence, taskCompletion),
    deviations: { change: implementerReport?.deviations || fixerReport?.deviations || [], verification: verifier?.deviations || [] },
  }
}

phase('Review')

const reviewScope = mode === 'build'
  ? `Review the complete committed diff against every READY-plan task and the verifier evidence.`
  : `Perform a delta-only confirmation, not a fresh audit. Check only owner-disposed fixes, disposition ` +
    `rationales, and regressions introduced in the touched area.`
const verificationContext = compactVerification(verifier, taskEvidence)
const priorContext = compactPriorReview()

const claudePrompt =
  `You are the Claude design/proportionality reviewer for ${planPath} in ${repo}. ${reviewScope} Check plan ` +
  `fit, non-goals, owner decisions, and whether the smallest correct patch was used. A blocker/major must ` +
  `identify an unsafe, contradictory, incorrect, incomplete task, or non-discriminating test—not a preference ` +
  `or desirable follow-up. Use IDs CLAUDE-001 onward, preserving prior IDs that remain. Return exact location, ` +
  `violated contract, and smallest correction. Return findings only: no diff summary, repeated task text, or ` +
  `process narrative. Do not edit.\n\nVerification:\n${JSON.stringify(verificationContext)}\n\n` +
  `Prior review:\n${JSON.stringify(priorContext)}\n\nOwner dispositions:\n${JSON.stringify(ownerDispositions)}`

const codexPrompt =
  `Independently review the committed diff ${baseBranch}...HEAD for ${planPath} in ${repo}. ${reviewScope} ` +
  `Verify correctness, callers, contracts, test meaningfulness, and every verifier task entry against repository ` +
  `source. Use IDs CODEX-001 onward, preserving prior IDs that remain. Cite exact file:line and return the ` +
  `violated contract and smallest sufficient correction. Do not block on preferences or follow-ups. Return ` +
  `findings only in under 6,000 characters: no diff summary, repeated task text, or process narrative. Do not ` +
  `edit.\n\nVerification:\n${JSON.stringify(verificationContext)}\n\nPrior review:\n` +
  `${JSON.stringify(priorContext)}\n\n` +
  `Owner dispositions:\n${JSON.stringify(ownerDispositions)}`

const promptFingerprint = fingerprint(codexPrompt)
const codexPromptPath = `sapphire-flow-implement-${mode}-${promptFingerprint}-codex-review.md`

const [claudeReview, codexReview] = await parallel([
  () => agent(
    claudePrompt,
    { label: `implement-claude-${mode}`, phase: 'Review', model: 'sonnet', effort: 'high', schema: REVIEW },
  ),
  () => agent(
    `You are a relay for a real Codex review. Use the Write tool to write only ${codexPromptPath}, with the ` +
    `exact contents between the CODEX_REVIEW_PROMPT tags below (exclude the tags). Then, in one foreground ` +
    `Bash tool call, run exactly ./scripts/codex-review.sh ${codexPromptPath}, capture its numeric exit and raw ` +
    `output, and set that Bash call's timeout to ${codexTimeoutMs}ms. Do not use heredocs, redirects, background ` +
    `execution, or a hand-written codex exec command. Do not edit tracked repository files or any ` +
    `other file. On timeout, ` +
    `non-zero exit, empty output, or unparseable verdict, retry once; then return reviewerFailed=true rather ` +
    `than a clean report. Always return the final numeric script exitCode. Otherwise transcribe Codex ` +
    `faithfully and include unedited output as rawVerdict. ` +
    `Add no opinion. After the final attempt, replace the temporary prompt file contents with temporary prompt cleared.` +
    `\n\n<CODEX_REVIEW_PROMPT>\n${codexPrompt}\n` +
    `</CODEX_REVIEW_PROMPT>`,
    { label: `implement-codex-${mode}`, phase: 'Review', model: 'sonnet', effort: 'low', schema: CODEX_REVIEW },
  ),
])

const reviewersComplete = usable(claudeReview) && codexUsable(codexReview)
const acceptedRisks = acceptedPriorFindings()
const reports = [claudeReview, codexReview]
if (riskClass === 'high') reports.push(additionalReview)
const hasBlocking = reports.some((report) =>
  blocking(report).some((finding) => !acceptedRisks.some((accepted) => sameFinding(finding, accepted))),
)
const highRiskComplete = riskClass === 'ordinary' || usable(additionalReview)
const engineeringReview = !reviewersComplete || !highRiskComplete
  ? 'INCOMPLETE'
  : hasBlocking
    ? 'BLOCKED'
    : 'PASS'

phase('Finalize')

const staleAtEnd = await agent(
  stalenessPrompt('FINALIZE'),
  { label: 'implement-staleness-finalize', phase: 'Finalize', model: 'sonnet', effort: 'low', schema: STALENESS },
)
const planWentStale = stale(staleAtEnd)
const prReady = readinessOk && tasksComplete && verificationPassed && reviewersComplete &&
  gatesPassed && branchSafe && planUnchanged && finalReadinessOk &&
  !hasBlocking && !planWentStale && highRiskComplete
const ownerAction = mode === 'build' && reviewersComplete && hasBlocking
  ? 'OWNER_DECISION_REQUIRED'
  : null

return {
  planPath,
  mode,
  riskClass,
  baseBranch,
  taskCompletion,
  engineeringReview,
  recommendation: prReady ? 'PR_READY' : 'NOT_READY',
  ownerAction,
  reviewedCommit: verifiedHead,
  planFingerprint: manifest.documentFingerprint,
  taskEvidence,
  gateEvidence: verifier.gateEvidence,
  reviews: {
    claude: publicReview(claudeReview),
    codex: publicReview(codexReview),
    additional: publicReview(additionalReview),
  },
  ownerDispositions,
  acceptedRisks,
  deviations: { change: implementerReport?.deviations || fixerReport?.deviations || [], verification: verifier.deviations },
  planWentStale,
}
