"""Plan 180: the CI vulnerability gate must say what it found.

Prose assertions on the real ``ci.yml`` / ``.trivyignore`` / ``security.md`` /
``docs/plans/064-supply-chain-hardening.md`` text — the same "single selector
each, not a bespoke parser" style as
``tests/unit/tooling/test_recap_wheel_guard.py`` (Plan 082 Task 2H).

These lock the incident's two root causes staying fixed:

1. The gate step must be a `trivy convert` invocation that prints a table
   (no file-only output) — never a step that writes SARIF/JSON to a file
   *and* gates, which is what printed nothing but ``exit code 1`` in the
   2026-08-17 incident.
2. The SARIF must reach GitHub code scanning via
   ``github/codeql-action/upload-sarif``, including on the failure path.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _ci_yml_text() -> str:
    return (_REPO_ROOT / ".github/workflows/ci.yml").read_text()


def _trivyignore_text() -> str:
    return (_REPO_ROOT / ".trivyignore").read_text()


def _security_md_text() -> str:
    return (_REPO_ROOT / "docs/standards/security.md").read_text()


def _plan_064_text() -> str:
    return (_REPO_ROOT / "docs/plans/064-supply-chain-hardening.md").read_text()


def _build_image_and_scan_job_text() -> str:
    """Slice out just the `build-image-and-scan:` job body.

    Avoids false negatives/positives from the unrelated `lint` job's
    filesystem-scan Trivy step, which the plan explicitly does not touch.
    """
    text = _ci_yml_text()
    start = text.index("build-image-and-scan:")
    # Bounded by the next top-level job's leading comment block.
    end = text.index("\n  e2e:") if "\n  e2e:" in text else len(text)
    return text[start:end]


class TestScanOnceDeriveMany:
    """T1 — restructure the image-scan gate into scan-once/derive-many."""

    def test_report_step_writes_json_and_is_explicitly_non_gating(self) -> None:
        job = _build_image_and_scan_job_text()
        assert re.search(r"format:\s*json", job), (
            "expected a `trivy image` step writing a JSON report — the single "
            "source the table/SARIF/gate all derive from"
        )
        assert re.search(r'exit-code:\s*"0"', job), (
            "the report-writing step must be EXPLICITLY non-gating "
            '(exit-code: "0"), per the plan\'s trap #1 — it must say which '
            "failure mode it means, not omit exit-code and rely on an "
            "implicit default"
        )

    def test_report_step_keeps_ignore_unfixed_true(self) -> None:
        job = _build_image_and_scan_job_text()
        assert "format: json" in job, (
            "no JSON-report step found — cannot check ignore-unfixed near it"
        )
        json_step_start = job.index("format: json")
        # ignore-unfixed must be configured on/near the JSON-writing step,
        # since `trivy convert` has no --ignore-unfixed flag at all — it can
        # only be applied at scan time (verified empirically, Plan 180 T1).
        window = job[json_step_start : json_step_start + 800]
        assert "ignore-unfixed: true" in window, (
            "ignore-unfixed must stay true on the report step — T1 changes "
            "*where output goes*, never *what counts as a finding*"
        )

    def test_gate_is_a_convert_invocation_that_prints_a_table(self) -> None:
        job = _build_image_and_scan_job_text()
        assert "trivy convert" in job, (
            "D1: gate on `trivy convert`, not the original scan step — "
            "keeping the old gate re-introduces the divergence this design "
            "removes"
        )
        gate_match = re.search(r"trivy convert[^\n]*(?:\n[^\n]*)*?--exit-code 1", job)
        assert gate_match, "no `trivy convert ... --exit-code 1` gate found"
        gate_text = gate_match.group(0)
        assert "--format table" in gate_text, (
            "the gate must render `table` — the same command that fails the "
            "job must be the one that prints the human-readable findings"
        )
        assert "--output" not in gate_text and " -o " not in gate_text, (
            "the gate must print straight to the job log (no file output) — "
            "a file-output invocation is exactly what printed nothing in "
            "the 2026-08-17 incident"
        )

    def test_no_step_combines_sarif_format_with_a_gating_exit_code(self) -> None:
        """Regression lock for the exact incident shape: format: sarif +
        output-to-file + exit-code: "1", which prints nothing to the log."""
        job = _build_image_and_scan_job_text()
        for step_text in re.split(r"\n(?=      - name:)", job):
            if re.search(r"format:\s*sarif", step_text):
                assert not re.search(r'exit-code:\s*"?1"?\s*$', step_text, re.M), (
                    "a SARIF-writing step must never also be the gate: "
                    f"{step_text[:200]!r}"
                )

    def test_severity_filter_present_on_the_gate(self) -> None:
        job = _build_image_and_scan_job_text()
        gate_match = re.search(r"trivy convert[^\n]*(?:\n[^\n]*)*?--exit-code 1", job)
        assert gate_match
        assert "--severity HIGH,CRITICAL" in gate_match.group(0), (
            "the severity filter must be applied on the convert/gate path "
            "directly — `limit-severities-for-sarif` only ever governed the "
            "SARIF-writing path per its own comment, and does not carry the "
            "severity filter once the gate scans to JSON (Plan 180 T1 note)"
        )


class TestSarifReachesCodeScanning:
    """T2 — upload the SARIF to code scanning, including on failure."""

    def test_upload_sarif_action_present(self) -> None:
        job = _build_image_and_scan_job_text()
        assert "github/codeql-action/upload-sarif@" in job, (
            "the workflow never calls github/codeql-action/upload-sarif — "
            "SARIF's only consumer today is upload-artifact, which archives "
            "a zip nothing reads (the incident's second root cause)"
        )

    def test_upload_sarif_references_the_trivy_image_sarif_file(self) -> None:
        job = _build_image_and_scan_job_text()
        assert "github/codeql-action/upload-sarif@" in job, (
            "upload-sarif step is missing entirely — cannot check what it references"
        )
        idx = job.index("github/codeql-action/upload-sarif@")
        window = job[idx : idx + 400]
        assert "trivy-image.sarif" in window

    def test_upload_sarif_step_is_not_success_only(self) -> None:
        """It must run on the failure path (that's the run that matters)."""
        job = _build_image_and_scan_job_text()
        assert "github/codeql-action/upload-sarif@" in job, (
            "upload-sarif step is missing entirely — cannot check its `if:`"
        )
        idx = job.index("github/codeql-action/upload-sarif@")
        # Look at the step block: from the preceding "- name:"/"if:" lines
        # up to the `uses:` line itself.
        preceding = job[:idx]
        step_start = preceding.rindex("\n      - name:")
        step_block = job[step_start:idx]
        assert "if:" in step_block, (
            "the upload-sarif step needs an explicit `if:` that survives a "
            "prior step failing (e.g. `!cancelled()` / `always()`) — the "
            "default implicit `success()` condition would skip it exactly "
            "on the failure path that matters"
        )
        assert re.search(r"success\(\)\s*$", step_block.strip()) is None
        assert "always()" in step_block or "cancelled()" in step_block

    def test_upload_sarif_has_no_blanket_continue_on_error(self) -> None:
        job = _build_image_and_scan_job_text()
        assert "github/codeql-action/upload-sarif@" in job, (
            "upload-sarif step is missing entirely — cannot check its continue-on-error"
        )
        idx = job.index("github/codeql-action/upload-sarif@")
        preceding = job[:idx]
        step_start = preceding.rindex("\n      - name:")
        window = job[step_start : idx + 400]
        assert "continue-on-error: true" not in window, (
            "a blanket continue-on-error on the upload would permanently "
            "hide a broken upload (bad token, malformed SARIF, path typo) — "
            "the same silent-failure class this plan exists to close, moved "
            "one level down (Plan 180 traps)"
        )

    def test_job_declares_security_events_write_permission(self) -> None:
        job = _build_image_and_scan_job_text()
        assert "security-events: write" in job, (
            "upload-sarif requires the security-events: write permission on "
            "the job's GITHUB_TOKEN"
        )


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
