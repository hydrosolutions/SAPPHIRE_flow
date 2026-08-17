"""Plan 180: the CI vulnerability gate must say what it found.

Structural assertions over the parsed ``ci.yml`` (steps selected by ``id``/
``uses``, never by regex over the raw text — a regex keyed on a substring
like ``"trivy convert"`` can match an explanatory comment instead of the real
step, and a non-greedy span stops at the first flag it is told to find,
silently ignoring anything after it) plus prose assertions on
``.trivyignore`` / ``security.md`` / ``docs/plans/064-supply-chain-hardening.md``
— the same "single selector each, not a bespoke parser" style as
``tests/unit/tooling/test_recap_wheel_guard.py`` (Plan 082 Task 2H), now via
``yaml.safe_load`` like ``tests/unit/test_compose_schedule_default.py``.

These lock the incident's two root causes staying fixed:

1. The gate step must be a `trivy convert` invocation that prints a table
   (no file-only output) — never a step that writes SARIF/JSON to a file
   *and* gates, which is what printed nothing but ``exit code 1`` in the
   2026-08-17 incident.
2. The SARIF must reach GitHub code scanning via
   ``github/codeql-action/upload-sarif``, including on the failure path —
   proved by actually evaluating the `if:` expressions against representative
   step-outcome scenarios, not by pattern-matching the condition string.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_JOB_NAME = "build-image-and-scan"


def _ci_yml_text() -> str:
    return (_REPO_ROOT / ".github/workflows/ci.yml").read_text()


def _trivyignore_text() -> str:
    return (_REPO_ROOT / ".trivyignore").read_text()


def _security_md_text() -> str:
    return (_REPO_ROOT / "docs/standards/security.md").read_text()


def _plan_064_text() -> str:
    return (_REPO_ROOT / "docs/plans/064-supply-chain-hardening.md").read_text()


def _build_image_and_scan_job() -> dict[str, Any]:
    workflow = yaml.safe_load(_ci_yml_text())
    return workflow["jobs"][_JOB_NAME]


def _steps_by_id(job: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {step["id"]: step for step in job["steps"] if step.get("id")}


def _step_by_id(job: dict[str, Any], step_id: str) -> dict[str, Any]:
    steps = _steps_by_id(job)
    assert step_id in steps, f"no step with id: {step_id!r} in job {_JOB_NAME!r}"
    return steps[step_id]


def _step_by_uses_prefix(job: dict[str, Any], prefix: str) -> dict[str, Any]:
    for step in job["steps"]:
        if str(step.get("uses", "")).startswith(prefix):
            return step
    raise AssertionError(
        f"no step with `uses:` starting {prefix!r} in job {_JOB_NAME!r}"
    )


def _eval_gha_if(expr: str, *, job_status: str, outcomes: dict[str, str]) -> bool:
    """Evaluate the small subset of GitHub Actions `if:` syntax this workflow uses.

    ``job_status`` is what ``cancelled()``/``success()``/``failure()`` (called
    with no arguments) resolve against — GitHub Actions defines those as the
    status of the job *as of this point*, not any single prior step.
    ``outcomes`` maps step id -> 'success' | 'failure' | 'skipped', used for
    ``steps.<id>.outcome`` references.
    """
    body = expr.strip()
    if body.startswith("${{") and body.endswith("}}"):
        body = body[3:-2].strip()
    body = re.sub(r"steps\.([A-Za-z0-9_-]+)\.outcome", r"steps['\1']", body)
    body = body.replace("!=", "__NE__").replace("!", " not ").replace("__NE__", "!=")
    body = body.replace("&&", " and ").replace("||", " or ")
    namespace = {
        "cancelled": lambda: job_status == "cancelled",
        "success": lambda: job_status == "success",
        "failure": lambda: job_status == "failure",
        "always": lambda: True,
        "steps": outcomes,
    }
    return bool(eval(body, {"__builtins__": {}}, namespace))  # noqa: S307 - fixed test-only grammar


class TestScanOnceDeriveMany:
    """T1 — restructure the image-scan gate into scan-once/derive-many."""

    def test_report_step_writes_json_and_is_explicitly_non_gating(self) -> None:
        job = _build_image_and_scan_job()
        report = _step_by_id(job, "trivy-scan")["with"]
        assert report["format"] == "json", (
            "expected the report step to write a JSON report — the single "
            "source the table/SARIF/gate all derive from"
        )
        assert report["exit-code"] == "0", (
            "the report-writing step must be EXPLICITLY non-gating "
            '(exit-code: "0"), per the plan\'s trap #1 — it must say which '
            "failure mode it means, not omit exit-code and rely on an "
            "implicit default"
        )

    def test_report_step_keeps_ignore_unfixed_true(self) -> None:
        job = _build_image_and_scan_job()
        report = _step_by_id(job, "trivy-scan")["with"]
        # ignore-unfixed must live on the report step: `trivy convert` has no
        # --ignore-unfixed flag at all — it can only be applied at scan time
        # (verified empirically, Plan 180 T1).
        assert report["ignore-unfixed"] is True, (
            "ignore-unfixed must stay true on the report step — T1 changes "
            "*where output goes*, never *what counts as a finding*"
        )

    def test_gate_is_a_convert_invocation_that_prints_a_table(self) -> None:
        job = _build_image_and_scan_job()
        gate = _step_by_id(job, "trivy-gate-table")
        run_text = gate["run"]
        assert "trivy convert" in run_text, (
            "D1: gate on `trivy convert`, not the original scan step — "
            "keeping the old gate re-introduces the divergence this design "
            "removes"
        )
        assert "--format table" in run_text, (
            "the gate must render `table` — the same command that fails the "
            "job must be the one that prints the human-readable findings"
        )
        assert "--exit-code 1" in run_text, (
            "the gate step must fail the job on a finding"
        )
        assert "trivy-image.json" in run_text, (
            "the gate must convert the SAME report the scan step wrote"
        )
        # Checked over the step's FULL run text (not a regex span truncated
        # at the first `--exit-code 1`) so a flag added anywhere else in the
        # command — e.g. `--output silent.txt` after --exit-code — is caught.
        assert (
            "--output" not in run_text
            and re.search(r"(?:^|\s)-o(?:\s|$)", run_text) is None
        ), (
            "the gate must print straight to the job log (no file output) — "
            "a file-output invocation is exactly what printed nothing in "
            "the 2026-08-17 incident"
        )

    def test_no_step_combines_sarif_output_with_a_gating_exit_code(self) -> None:
        """Regression lock for the exact incident shape: a SARIF-writing step
        (`with: {format: sarif}` on the action-form, or `--format sarif` on
        the CLI-form `trivy convert` this plan introduced) that ALSO gates
        via a nonzero exit-code, which prints nothing to the log."""
        job = _build_image_and_scan_job()
        for step in job["steps"]:
            with_: dict[str, Any] = step.get("with", {}) or {}
            run_text: str = step.get("run", "") or ""
            declares_sarif = (
                with_.get("format") == "sarif" or "--format sarif" in run_text
            )
            if not declares_sarif:
                continue
            action_exit_code = str(with_.get("exit-code", "0"))
            assert action_exit_code == "0", (
                f"step {step.get('name')!r} writes SARIF and also gates via "
                "`with: exit-code` — a SARIF-writing step must never be the gate"
            )
            assert "--exit-code" not in run_text, (
                f"step {step.get('name')!r} writes SARIF via `trivy convert "
                "--format sarif` and also passes --exit-code — a SARIF-writing "
                "step must never be the gate"
            )

    def test_severity_filter_present_on_the_gate(self) -> None:
        job = _build_image_and_scan_job()
        run_text = _step_by_id(job, "trivy-gate-table")["run"]
        assert "--severity HIGH,CRITICAL" in run_text, (
            "the severity filter must be applied on the convert/gate path "
            "directly — `limit-severities-for-sarif` only ever governed the "
            "SARIF-writing path per its own comment, and does not carry the "
            "severity filter once the gate scans to JSON (Plan 180 T1 note)"
        )


class TestSarifReachesCodeScanning:
    """T2 — upload the SARIF to code scanning, including on failure."""

    def test_upload_sarif_action_present(self) -> None:
        job = _build_image_and_scan_job()
        # Raises AssertionError with a clear message if absent.
        _step_by_uses_prefix(job, "github/codeql-action/upload-sarif@")

    def test_upload_sarif_references_the_trivy_image_sarif_file(self) -> None:
        job = _build_image_and_scan_job()
        upload = _step_by_uses_prefix(job, "github/codeql-action/upload-sarif@")
        assert upload["with"]["sarif_file"] == "trivy-image.sarif"

    def test_upload_sarif_has_no_blanket_continue_on_error(self) -> None:
        job = _build_image_and_scan_job()
        upload = _step_by_uses_prefix(job, "github/codeql-action/upload-sarif@")
        assert upload.get("continue-on-error") is not True, (
            "a blanket continue-on-error on the upload would permanently "
            "hide a broken upload (bad token, malformed SARIF, path typo) — "
            "the same silent-failure class this plan exists to close, moved "
            "one level down (Plan 180 traps)"
        )

    def test_job_declares_security_events_write_permission(self) -> None:
        job = _build_image_and_scan_job()
        assert job.get("permissions", {}).get("security-events") == "write", (
            "upload-sarif requires the security-events: write permission on "
            "the job's GITHUB_TOKEN"
        )


class TestFailurePathReallyPublishes:
    """T2 (failure path) — proved by EVALUATING the `if:` expressions of the
    SARIF-conversion step and the code-scanning upload step against
    representative step-outcome scenarios, not by matching substrings in the
    condition text. This is what actually distinguishes a correct condition
    from a plausible-looking-but-wrong one: `if: ${{ cancelled() }}` and
    `if: ${{ !cancelled() && success() }}` both "mention" cancelled()/lack a
    bare success()-only form, yet both would skip the gate-failed scenario
    below — this class fails on either of those.
    """

    def _conditions(self) -> tuple[str, str]:
        # GitHub Actions' own default when a step omits `if:` entirely is a
        # bare `success()` — that IS the incident's failure mode (removing
        # the override silently restores it), so a missing key must resolve
        # to that string, never raise or skip the check.
        job = _build_image_and_scan_job()
        sarif_if = _step_by_id(job, "trivy-sarif").get("if", "success()")
        upload_if = _step_by_uses_prefix(job, "github/codeql-action/upload-sarif@").get(
            "if", "success()"
        )
        return sarif_if, upload_if

    def test_gate_failed_not_cancelled_conversion_and_upload_both_run(self) -> None:
        """The failure path that matters: a real CVE tripped the gate."""
        sarif_if, upload_if = self._conditions()
        outcomes = {"trivy-scan": "success", "trivy-gate-table": "failure"}
        sarif_runs = _eval_gha_if(sarif_if, job_status="failure", outcomes=outcomes)
        assert sarif_runs, "SARIF conversion must still run when the gate step failed"
        outcomes["trivy-sarif"] = "success" if sarif_runs else "skipped"
        upload_runs = _eval_gha_if(upload_if, job_status="failure", outcomes=outcomes)
        assert upload_runs, "the SARIF upload must still run when the gate step failed"

    def test_scan_itself_failed_neither_conversion_nor_upload_run(self) -> None:
        """An operational scan failure (not a finding) must not fabricate a
        report from a JSON file that was never written."""
        sarif_if, upload_if = self._conditions()
        outcomes = {"trivy-scan": "failure"}
        sarif_runs = _eval_gha_if(sarif_if, job_status="failure", outcomes=outcomes)
        assert not sarif_runs, (
            "conversion must not run off a report the scan never wrote"
        )
        outcomes["trivy-sarif"] = "success" if sarif_runs else "skipped"
        upload_runs = _eval_gha_if(upload_if, job_status="failure", outcomes=outcomes)
        assert not upload_runs, "upload must not run without a SARIF file to upload"

    def test_cancelled_run_neither_conversion_nor_upload_run(self) -> None:
        sarif_if, upload_if = self._conditions()
        outcomes = {"trivy-scan": "cancelled"}
        sarif_runs = _eval_gha_if(sarif_if, job_status="cancelled", outcomes=outcomes)
        assert not sarif_runs, "a cancelled run must not still convert/upload"
        outcomes["trivy-sarif"] = "success" if sarif_runs else "skipped"
        upload_runs = _eval_gha_if(upload_if, job_status="cancelled", outcomes=outcomes)
        assert not upload_runs

    def test_normal_success_conversion_and_upload_both_run(self) -> None:
        sarif_if, upload_if = self._conditions()
        outcomes = {"trivy-scan": "success", "trivy-gate-table": "success"}
        sarif_runs = _eval_gha_if(sarif_if, job_status="success", outcomes=outcomes)
        assert sarif_runs
        outcomes["trivy-sarif"] = "success" if sarif_runs else "skipped"
        upload_runs = _eval_gha_if(upload_if, job_status="success", outcomes=outcomes)
        assert upload_runs


class TestTrivyignorePolicyIsInternallyConsistent:
    """T3 — .trivyignore's own instruction must not describe an impossible
    case."""

    def test_does_not_instruct_entries_for_not_yet_fixed_cves(self) -> None:
        text = _trivyignore_text()
        assert "not-yet-fixed" not in text, (
            "ignore-unfixed: true already excludes every CVE with no "
            "published fix from ever being reported — telling the reader to "
            "add a .trivyignore entry for a 'not-yet-fixed' CVE describes a "
            "case that cannot occur"
        )

    def test_describes_the_actual_case_a_fix_we_cannot_adopt(self) -> None:
        text = _trivyignore_text()
        assert "cannot adopt" in text or "cannot take" in text, (
            "D2: .trivyignore is for a published fix we cannot adopt "
            "(untracked suite / unmovable Python dep), not for unfixed CVEs"
        )


class TestSecurityMdDocumentsD2Policy:
    def test_documents_the_unadoptable_fix_policy(self) -> None:
        text = _security_md_text()
        idx = text.index("### CVE scanning layers")
        section = text[idx : idx + 3000]
        assert "cannot adopt" in section or "cannot take" in section, (
            "security.md's CVE scanning layers section must record D2's "
            "policy for a CVE with a published fix we cannot adopt"
        )

    def test_documents_expiry_is_a_dated_comment_not_enforced(self) -> None:
        text = _security_md_text()
        idx = text.index("### CVE scanning layers")
        section = text[idx : idx + 3000]
        assert "not" in section and "enforce" in section, (
            "D3: say plainly that the re-review date is a comment, not an "
            "enforced check — a comment does not expire by itself"
        )


class TestPlan064D4Reconciled:
    def test_no_longer_claims_ci_fails_on_unfixed_cves(self) -> None:
        text = _plan_064_text()
        assert "Both fail CI on HIGH+ unfixed." not in text, (
            "Plan 064 D4 said both scans 'fail CI on HIGH+ unfixed', but "
            "both run --ignore-unfixed, which excludes exactly those — two "
            "authoritative docs must not disagree (Plan 180 trap #4)"
        )
