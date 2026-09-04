"""Source guards for the task-evidenced implementation workflow."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
IMPLEMENT_WORKFLOW = REPO_ROOT / ".claude" / "workflows" / "implement.js"
WORKFLOW_POLICY = REPO_ROOT / "docs" / "workflow.md"
SHARED_SETTINGS = REPO_ROOT / ".claude" / "settings.json"
GITIGNORE = REPO_ROOT / ".gitignore"


def _source() -> str:
    return IMPLEMENT_WORKFLOW.read_text(encoding="utf-8")


def test_policy_requires_evidence_for_every_task() -> None:
    policy = " ".join(WORKFLOW_POLICY.read_text(encoding="utf-8").split())

    assert "Every READY-plan task receives independent evidence" in policy
    assert "beforeEvidence" in policy
    assert "afterEvidence" in policy
    assert "one complete exit-gate run per committed candidate" in policy
    assert "OWNER_DECISION_REQUIRED" in policy
    assert "Human authority" in policy


class TestImplementWorkflowContract:
    def test_build_and_confirm_replace_the_review_fix_loop(self) -> None:
        source = _source()

        assert "mode === 'build'" in source
        assert "mode === 'confirm'" in source
        assert "while (" not in source
        assert "maxRounds" not in source
        assert "delta-only" in source

    def test_readiness_uses_the_deterministic_checker_result(self) -> None:
        source = _source()

        assert "uv run python scripts/check_readiness.py" in source
        assert "'exitCode'" in source
        assert "'outputExcerpt'" in source
        assert "const readinessOk =" in source
        assert "readiness.exitCode === 0" in source

    def test_every_task_has_before_and_after_evidence(self) -> None:
        source = _source()

        for field in (
            "taskId",
            "preChangeFingerprint",
            "preChangeStatus",
            "preChangeExitCode",
            "preChangeOutputExcerpt",
            "verificationFingerprint",
            "exitCode",
            "outputExcerpt",
            "beforeEvidence",
            "afterEvidence",
            "status",
        ):
            assert f"'{field}'" in source
        assert "PLAN_INCOMPLETE" in source
        assert "taskIdsMatch" in source
        assert "implementationEvidenceHasContent" in source
        assert "verificationEvidenceHasContent" in source
        assert "preChangeEvidenceValid" in source
        assert "EXPECTED_FAILURE" in source

    def test_context_uses_compact_manifest(self) -> None:
        source = _source()

        assert "--inspect-json" in source
        assert "inspectionRawOutput" in source
        assert "parseManifest" in source
        assert "documentFingerprint" in source
        assert "planText" not in source
        assert "TASK_CONTRACT" not in source
        assert "gateEvidenceComplete" in source
        assert "const gatesPassed =" in source

    def test_implementer_targets_tasks_and_verifier_owns_full_gates(self) -> None:
        source = _source()

        assert "Do not run the plan's complete Exit gates" in source
        assert "Run every plan Exit gates command" in source
        assert "one complete exit-gate run per committed candidate" in source
        assert "const verificationPassed" in source

    def test_reviewers_are_separate_and_codex_is_real(self) -> None:
        source = _source()

        assert "claudeReview" in source
        assert "codexReview" in source
        assert "./scripts/codex-review.sh" in source
        assert "Do not use heredocs, redirects" in source
        assert "claude: publicReview(claudeReview)" in source
        assert "codex: publicReview(codexReview)" in source
        assert "CODEX_REVIEW" in source
        assert "codexUsable" in source
        assert "report.exitCode === 0" in source
        assert ".flatMap(" not in source

    def test_prompt_identity_is_runtime_safe(self) -> None:
        source = _source()

        assert "function fingerprint" in source
        assert "fingerprint(codexPrompt)" in source
        assert "0x811c9dc5" in source
        assert "Math.imul(value, 0x01000193)" in source
        assert "utf8Bytes(String(text))" in source
        assert (
            "sapphire-flow-implement-${mode}-${promptFingerprint}-codex-review.md"
            in source
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
        assert "/sapphire-flow-*-codex-review.md" in gitignore
        assert "Edit(sapphire-flow-*-codex-review.md)" in settings
        assert "Bash(./scripts/codex-review.sh sapphire-flow-implement-:*)" in settings

    def test_owner_disposition_precedes_the_only_fixer(self) -> None:
        source = _source()

        assert "ownerDispositions" in source
        assert "if (mode === 'confirm' && fixFindings.length > 0)" in source
        assert "OWNER_DECISION_REQUIRED" in source
        assert "acceptedRisks" in source
        assert "const acceptedRisks = acceptedPriorFindings()" in source
        assert "sameFinding" in source
        assert "sameUniqueStrings(dispositionIds, findingIds)" in source

    def test_legacy_ready_plan_has_an_on_demand_migration_path(self) -> None:
        source = _source()
        policy = WORKFLOW_POLICY.read_text(encoding="utf-8")

        assert "PLAN_CONTRACT_REQUIRED" in source
        assert "set a legacy or malformed READY plan to DRAFT" in source
        assert "readyPlanNeedsContract" in source
        assert "context.inspectionExitCode !== 1" in source
        assert "if (!context || !asSha(context.headSha))" in source
        assert "Legacy READY plans are migrated only when selected" in policy

    def test_confirmation_and_git_state_are_bound_fail_closed(self) -> None:
        source = _source()

        for field in (
            "riskClass",
            "baseBranch",
            "branchName",
            "tagCount",
            "tagFingerprint",
            "remoteBranchSha",
        ):
            assert field in source
        assert "priorReview.mode !== 'build'" in source
        assert "priorReview.planPath !== planPath" in source
        assert "priorReview.riskClass !== riskClass" in source
        assert "implement requires explicit riskClass" in source
        assert "const priorImplementationComplete =" in source
        assert "priorReview.planFingerprint !== manifest.documentFingerprint" in source
        assert "branchAtStart !== 'main'" in source
        assert "const branchSafe =" in source
        assert "const planUnchanged =" in source
        assert "planDiffExitCode === 0" in source
        assert "const finalReadinessOk =" in source

    def test_git_tag_state_uses_compact_identity(self) -> None:
        source = _source()

        assert "tagStateValid" in source
        assert "verifier.tagCount === context.tagCount" in source
        assert "verifier.tagFingerprint === context.tagFingerprint" in source
        assert "tagNames" not in source

    def test_review_prompts_use_compact_evidence(self) -> None:
        source = _source()

        assert "compactPriorReview" in source
        assert "compactVerification" in source
        assert "JSON.stringify(verificationContext)" in source
        assert "JSON.stringify(priorContext)" in source

    def test_pr_ready_is_fail_closed(self) -> None:
        source = _source()

        assert "const prReady =" in source
        for predicate in (
            "readinessOk",
            "tasksComplete",
            "verificationPassed",
            "reviewersComplete",
            "gatesPassed",
            "branchSafe",
            "planUnchanged",
            "finalReadinessOk",
            "!hasBlocking",
            "!planWentStale",
            "highRiskComplete",
        ):
            assert predicate in source
        assert "recommendation: prReady ? 'PR_READY' : 'NOT_READY'" in source
        assert "taskCompletion" in source
        assert "engineeringReview" in source

    def test_agent_outputs_are_bounded_and_terminal_is_deduplicated(self) -> None:
        source = _source()

        assert "maxLength: 1000" in source
        assert "outputExcerpt" in source
        assert "canonicalTaskEvidence" in source
        assert "publicReview" in source
        terminal = source.rsplit("return {", maxsplit=1)[-1]
        assert "planSnapshot:" not in terminal
        assert "implementerReport," not in terminal
        assert "fixerReport," not in terminal
        assert "verifier," not in terminal
        assert "rawVerdictPresent" in source
