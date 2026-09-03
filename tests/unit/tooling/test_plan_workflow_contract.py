"""Source guards for the bounded plan-review workflow."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PLAN_WORKFLOW = REPO_ROOT / ".claude" / "workflows" / "plan.js"
LEGACY_WORKFLOW = REPO_ROOT / ".claude" / "workflows" / "plan-review.js"
WORKFLOW_POLICY = REPO_ROOT / "docs" / "workflow.md"
SHARED_SETTINGS = REPO_ROOT / ".claude" / "settings.json"
GITIGNORE = REPO_ROOT / ".gitignore"


def _source() -> str:
    return PLAN_WORKFLOW.read_text(encoding="utf-8")


def test_policy_uses_tasks_as_the_only_completion_ledger() -> None:
    policy = WORKFLOW_POLICY.read_text(encoding="utf-8")

    assert "Tasks are the single completion ledger" in policy
    for field in ("Outcome", "In / Out", "Verification", "Pre-change"):
        assert f"**{field}:**" in policy
    assert "Do not add a second acceptance ledger" in policy


class TestPlanWorkflowContract:
    def test_review_and_confirm_replace_the_autonomous_loop(self) -> None:
        source = _source()

        assert "mode === 'review'" in source
        assert "mode === 'confirm'" in source
        assert "while (" not in source
        assert "maxRounds" not in source
        assert "revise-r" not in source

    def test_claude_and_real_codex_are_both_required(self) -> None:
        source = _source()

        assert "claudeReview" in source
        assert "codexReview" in source
        assert "./scripts/codex-review.sh" in source
        assert "REVIEW_INCOMPLETE" in source
        assert "reviewersComplete" in source
        assert "CODEX_REVIEW" in source
        assert "codexUsable" in source
        assert "report.exitCode === 0" in source

    def test_findings_carry_stable_contract_evidence(self) -> None:
        source = _source()

        for field in ("id", "severity", "location", "violatedContract", "correction"):
            assert f"'{field}'" in source
        assert "claude: publicReview(claudeReview)" in source
        assert "codex: publicReview(codexReview)" in source

    def test_context_uses_compact_manifest(self) -> None:
        source = _source()

        assert "--inspect-json" in source
        assert "inspectionRawOutput" in source
        assert "parseManifest" in source
        assert "documentFingerprint" in source
        assert "planText" not in source
        assert "taskContracts" not in source
        assert "reviewedPlanSnapshot" not in source

    def test_workflow_is_read_only_and_legacy_variant_is_removed(self) -> None:
        source = _source()

        assert "Do not edit tracked repository files or any other file" in source
        assert "heredocs, redirects, background execution" in source
        assert "planner" not in source.lower()
        assert not LEGACY_WORKFLOW.exists()

    def test_prompt_identity_is_runtime_safe(self) -> None:
        source = _source()

        assert "function fingerprint" in source
        assert "fingerprint(codexPrompt)" in source
        assert "0x811c9dc5" in source
        assert "Math.imul(value, 0x01000193)" in source
        assert "utf8Bytes(String(text))" in source
        assert (
            "sapphire-flow-plan-${mode}-${promptFingerprint}-codex-review.md" in source
        )
        assert "Date.now" not in source
        assert "Math.random" not in source
        assert "invocationToken" not in source

    def test_runtime_helpers_avoid_unavailable_globals(self) -> None:
        source = _source()

        assert "function utf8Bytes" in source
        assert "utf8Bytes(context.inspectionRawOutput).length" in source
        for unavailable in (
            "TextEncoder(",
            "Buffer.",
            "crypto.",
            "structuredClone(",
            "process.",
            "require(",
        ):
            assert unavailable not in source

    def test_codex_prompt_path_is_workspace_local(self) -> None:
        source = _source()
        settings = SHARED_SETTINGS.read_text(encoding="utf-8")
        gitignore = GITIGNORE.read_text(encoding="utf-8")

        assert "/tmp/sapphire-flow" not in source
        assert "`.claude/sapphire-flow" not in source
        assert "sapphire-flow-*-codex-review.md" in gitignore
        assert "Edit(sapphire-flow-*-codex-review.md)" in settings
        assert "Bash(./scripts/codex-review.sh sapphire-flow-plan-:*)" in settings

    def test_confirmation_is_one_delta_scoped_pass(self) -> None:
        source = _source()

        assert "not a fresh audit" in source
        assert "changed area" in source
        assert "ownerDispositions" in source
        assert "acceptedRisks" in source
        assert "const acceptedRisks = acceptedPriorFindings()" in source
        assert "sameFinding" in source
        assert "sameUniqueStrings(dispositionIds, findingIds)" in source

    def test_confirmation_is_bound_to_the_prior_packet(self) -> None:
        source = _source()

        assert "plan requires explicit riskClass" in source
        assert "priorReview.mode !== 'review'" in source
        assert "priorReview.planPath !== planPath" in source
        assert "priorReview.riskClass !== riskClass" in source
        assert "validFingerprint(priorReview.planFingerprint)" in source
        assert "riskClass," in source

    def test_confirmation_accepts_changed_validated_plan(self) -> None:
        source = _source()

        assert "validFingerprint(priorReview.planFingerprint)" in source
        assert "planFingerprint: priorReview.planFingerprint" in source
        assert "documentFingerprint: manifest.documentFingerprint" in source
        assert (
            "priorReview.planFingerprint !== manifest.documentFingerprint" not in source
        )

    def test_confirmation_prompt_omits_raw_prior_reports(self) -> None:
        source = _source()

        assert "compactPriorReview" in source
        assert "JSON.stringify(priorContext)" in source
        assert "reviewedPlanSnapshot" not in source

    def test_codex_success_requires_raw_process_evidence(self) -> None:
        source = _source()

        assert "CODEX_REVIEW" in source
        assert "codexUsable" in source
        assert "report.exitCode === 0" in source
        assert "report.rawVerdict.trim().length > 0" in source

    def test_reviewer_failure_cannot_recommend_ready(self) -> None:
        source = _source()

        assert "const reviewersComplete" in source
        assert "reviewersComplete ? 'READY' : 'REVIEW_INCOMPLETE'" in source
        assert (
            "if (mode === 'review' && reviewersComplete) recommendation = 'NOT_READY'"
            in source
        )
        assert "CONFIRM_REQUIRED" in source
