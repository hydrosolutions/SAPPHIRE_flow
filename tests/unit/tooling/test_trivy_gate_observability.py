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

import json
import re
import subprocess
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


def _eval_gha_if(
    expr: str,
    *,
    job_status: str,
    outcomes: dict[str, str],
    outputs: dict[str, dict[str, str]] | None = None,
) -> bool:
    """Evaluate the small subset of GitHub Actions `if:` syntax this workflow uses.

    ``job_status`` is what ``cancelled()``/``success()``/``failure()`` (called
    with no arguments) resolve against — GitHub Actions defines those as the
    status of the job *as of this point*, not any single prior step.
    ``outcomes`` maps step id -> 'success' | 'failure' | 'skipped', used for
    ``steps.<id>.outcome`` references. ``outputs`` maps step id -> {name: value}
    for ``steps.<id>.outputs.<name>`` references (Plan 309's chain gates the scan
    on whether any download attempt produced a syft binary). A step that did not
    run has no outputs, and GitHub resolves such a reference to the empty string
    rather than erroring — modelled here, because a condition that reads an
    output of a skipped step is a real and easy mistake.
    """
    body = expr.strip()
    if body.startswith("${{") and body.endswith("}}"):
        body = body[3:-2].strip()

    # GitHub prepends an implicit `success()` to any `if:` that contains NO status
    # function — so `steps.x.outcome == 'success'` alone still skips once an earlier
    # step has failed. Modelling this matters: without it a condition that drops
    # `!cancelled()` would pass here while real Actions silently skips the step after
    # the gate fails, which is exactly the invisible-failure class Plan 180 exists to
    # close. See the plan's "Traps" §1.
    has_status_fn = re.search(r"\b(success|failure|cancelled|always)\s*\(", body)
    if not has_status_fn and job_status != "success":
        return False
    # OUTPUTS first: `steps.x.outputs.y` would otherwise be half-rewritten by the
    # `.outcome` rule below and then parsed as attribute access on a dict.
    body = re.sub(
        r"steps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)",
        r"step_outputs['\1']['\2']",
        body,
    )
    body = re.sub(r"steps\.([A-Za-z0-9_-]+)\.outcome", r"steps['\1']", body)
    body = body.replace("!=", "__NE__").replace("!", " not ").replace("__NE__", "!=")
    body = body.replace("&&", " and ").replace("||", " or ")
    namespace = {
        "cancelled": lambda: job_status == "cancelled",
        "success": lambda: job_status == "success",
        "failure": lambda: job_status == "failure",
        "always": lambda: True,
        "steps": outcomes,
        "step_outputs": _StepOutputs(outputs or {}),
    }
    return bool(eval(body, {"__builtins__": {}}, namespace))  # noqa: S307 - fixed test-only grammar


class _StepOutputs(dict[str, Any]):
    """``steps.<id>.outputs.<name>`` for a step that never ran resolves to the
    empty string in real Actions, not to an error. Both levels default, because
    reading an output of a *skipped* step is an easy and realistic mistake and a
    KeyError here would look like a test bug rather than a workflow one."""

    def __missing__(self, key: str) -> Any:
        return _StepOutputs({})

    def __getitem__(self, key: str) -> Any:
        value = super().get(key, _MISSING)
        if value is _MISSING:
            return _StepOutputs({})
        return _StepOutputs(value) if isinstance(value, dict) else value

    def __eq__(self, other: object) -> bool:
        # An absent output compares equal to "" and to nothing else.
        if isinstance(other, str):
            return not self and other == ""
        return super().__eq__(other)

    __hash__ = None  # type: ignore[assignment]


_MISSING = object()


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
        outcomes = {
            "trivy-scan": "success",
            "trivy-report": "success",
            "trivy-gate-table": "failure",
        }
        sarif_runs = _eval_gha_if(sarif_if, job_status="failure", outcomes=outcomes)
        assert sarif_runs, "SARIF conversion must still run when the gate step failed"
        outcomes["trivy-sarif"] = "success" if sarif_runs else "skipped"
        upload_runs = _eval_gha_if(upload_if, job_status="failure", outcomes=outcomes)
        assert upload_runs, "the SARIF upload must still run when the gate step failed"

    def test_scan_itself_failed_neither_conversion_nor_upload_run(self) -> None:
        """An operational scan failure (not a finding) must not fabricate a
        report from a JSON file that was never written."""
        sarif_if, upload_if = self._conditions()
        # Both attempts failed, so the enforcement step failed and no report
        # exists. Plan 309 T2 (ii) added the retry between them; the property is
        # unchanged — nothing downstream may run off a report nobody wrote.
        outcomes = {
            "trivy-scan": "failure",
            "trivy-scan-2": "failure",
            "trivy-report": "failure",
        }
        sarif_runs = _eval_gha_if(sarif_if, job_status="failure", outcomes=outcomes)
        assert not sarif_runs, (
            "conversion must not run off a report the scan never wrote"
        )
        outcomes["trivy-sarif"] = "success" if sarif_runs else "skipped"
        upload_runs = _eval_gha_if(upload_if, job_status="failure", outcomes=outcomes)
        assert not upload_runs, "upload must not run without a SARIF file to upload"

    def test_a_recovered_scan_still_converts_and_uploads(self) -> None:
        """Plan 309 T2 (ii): attempt 1 failed to fetch trivy, attempt 2 worked.
        The report exists, so everything downstream must run exactly as on a
        first-attempt success — gating on attempt 1 alone would silently drop
        the SARIF after a recovery."""
        sarif_if, upload_if = self._conditions()
        outcomes = {
            "trivy-scan": "failure",
            "trivy-scan-2": "success",
            "trivy-report": "success",
            "trivy-gate-table": "success",
        }
        sarif_runs = _eval_gha_if(sarif_if, job_status="success", outcomes=outcomes)
        assert sarif_runs, "a recovered scan must still produce SARIF"
        outcomes["trivy-sarif"] = "success" if sarif_runs else "skipped"
        assert _eval_gha_if(upload_if, job_status="success", outcomes=outcomes), (
            "a recovered scan must still reach code scanning"
        )

    def test_the_gating_scan_is_not_retried(self) -> None:
        """🔴 The distinction the retry turns on. `lint`'s filesystem scan
        carries `exit-code: "1"` — it IS the gate — so retrying it would re-run
        a real CVE finding. Only the image scan, which is explicitly
        non-gating (`exit-code: "0"`), may be retried."""
        lint_steps = yaml.safe_load(_ci_yml_text())["jobs"]["lint"]["steps"]
        fs_scan = next(
            s
            for s in lint_steps
            if str(s.get("uses", "")).startswith("aquasecurity/trivy-action@")
        )
        assert fs_scan["with"]["exit-code"] == "1", "precondition: it gates"
        assert "continue-on-error" not in fs_scan, (
            "the GATING filesystem scan must never be continue-on-error — a "
            "retry there re-runs a genuine CVE finding"
        )
        image_scan = _step_by_id(_build_image_and_scan_job(), "trivy-scan")
        assert image_scan["with"]["exit-code"] == "0", (
            "only a scan that cannot fail on findings may be retried"
        )
        assert image_scan["continue-on-error"] is True

    def test_cancelled_run_neither_conversion_nor_upload_run(self) -> None:
        sarif_if, upload_if = self._conditions()
        outcomes = {"trivy-scan": "cancelled", "trivy-report": "cancelled"}
        sarif_runs = _eval_gha_if(sarif_if, job_status="cancelled", outcomes=outcomes)
        assert not sarif_runs, "a cancelled run must not still convert/upload"
        outcomes["trivy-sarif"] = "success" if sarif_runs else "skipped"
        upload_runs = _eval_gha_if(upload_if, job_status="cancelled", outcomes=outcomes)
        assert not upload_runs

    def test_normal_success_conversion_and_upload_both_run(self) -> None:
        sarif_if, upload_if = self._conditions()
        outcomes = {
            "trivy-scan": "success",
            "trivy-report": "success",
            "trivy-gate-table": "success",
        }
        sarif_runs = _eval_gha_if(sarif_if, job_status="success", outcomes=outcomes)
        assert sarif_runs
        outcomes["trivy-sarif"] = "success" if sarif_runs else "skipped"
        upload_runs = _eval_gha_if(upload_if, job_status="success", outcomes=outcomes)
        assert upload_runs


_DOWNLOAD_IDS = ("syft-dl-1", "syft-dl-2", "syft-dl-3")
_WAIT_NAMES = (
    "Wait 4 minutes before retrying the syft download",
    "Wait 6 minutes before the final syft download attempt",
)


def _step_by_name(job: dict[str, Any], name: str) -> dict[str, Any]:
    for step in job["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r} in job {_JOB_NAME!r}")


def _simulate_sbom_chain(
    *,
    job_status: str,
    build_outcome: str,
    downloads: tuple[str, str, str] = ("success", "success", "success"),
    scan: str = "success",
    artifact_valid: bool = True,
) -> dict[str, str]:
    """Walk Plan 309's download -> execute -> enforce chain the way the runner
    does: in order, each step's `if:` evaluated against the outcomes and outputs
    accumulated so far.

    Returns step id (and wait-step name) -> 'success' | 'failure' | 'skipped'.
    Testing the conditions in isolation would miss the thing that actually
    matters — that a *skipped* step's outcome feeds the next condition.
    """
    job = _build_image_and_scan_job()
    outcomes: dict[str, str] = {"build-image": build_outcome}
    outputs: dict[str, dict[str, str]] = {}
    result: dict[str, str] = {}

    def run(step: dict[str, Any], key: str, would_be: str) -> bool:
        expr = step.get("if", "success()")
        ran = _eval_gha_if(
            expr, job_status=job_status, outcomes=outcomes, outputs=outputs
        )
        result[key] = would_be if ran else "skipped"
        if step.get("id"):
            outcomes[step["id"]] = result[key]
        return ran

    for i, step_id in enumerate(_DOWNLOAD_IDS):
        if i:
            wait = _step_by_name(job, _WAIT_NAMES[i - 1])
            run(wait, _WAIT_NAMES[i - 1], "success")
        run(_step_by_id(job, step_id), step_id, downloads[i])

    resolve = _step_by_id(job, "syft-cmd")
    if run(resolve, "syft-cmd", "success"):
        installed = any(result.get(sid) == "success" for sid in _DOWNLOAD_IDS)
        outputs["syft-cmd"] = {"installed": "true" if installed else "false"}

    run(_step_by_id(job, "sbom-generate"), "sbom-generate", scan)
    run(
        _step_by_id(job, "sbom-require"),
        "sbom-require",
        "success"
        if (result["sbom-generate"] == "success" and artifact_valid)
        else "failure",
    )
    run(_step_by_id(job, "sbom-upload"), "sbom-upload", "success")
    return result


class TestSbomSurvivesAGateFailure:
    """Plan 207 T2, re-expressed over Plan 309's three-part chain.

    The property is unchanged and must stay unchanged: the SBOM steps must not
    inherit the implicit ``success()`` that follows a gate failure. Plan 309
    widened the surface from two steps to seven, so every one of them now has to
    carry ``!cancelled() && steps.build-image.outcome == 'success'`` — and a
    condition that drops those would skip the retries *and* the enforcement step
    underneath an already-red CVE gate, which is the same invisible-failure class
    one level further down.

    Steps are selected by ``id`` (never by ``uses:`` prefix — ``Upload SBOM
    artifact`` shares a byte-identical pin with ``Upload Trivy SARIF artifact``).
    """

    def test_normal_success_whole_chain_runs(self) -> None:
        r = _simulate_sbom_chain(job_status="success", build_outcome="success")
        assert r["syft-dl-1"] == "success"
        assert r["syft-dl-2"] == "skipped", "attempt 2 must not run after a success"
        assert r["sbom-generate"] == "success"
        assert r["sbom-require"] == "success"
        assert r["sbom-upload"] == "success"

    def test_gate_failed_whole_chain_still_runs_and_uploads(self) -> None:
        """🔑 Plan 207's reason for existing, and Plan 309's biggest risk of
        undoing it. Verified against real CI as well — run 35623093354."""
        r = _simulate_sbom_chain(job_status="failure", build_outcome="success")
        assert r["syft-dl-1"] == "success", (
            "the syft download must still run when the vulnerability gate has "
            "failed the job — this is the run where the inventory matters most"
        )
        assert r["syft-cmd"] == "success"
        assert r["sbom-generate"] == "success"
        assert r["sbom-require"] == "success", (
            "the enforcement step must still run under a red gate, or an SBOM "
            "failure disappears behind an unrelated red check"
        )
        assert r["sbom-upload"] == "success", (
            "the SBOM must still be uploaded when the gate fails"
        )

    def test_gate_failed_the_retries_still_run_too(self) -> None:
        """The widened surface: it is not enough for the first attempt to
        survive a red job — the retry chain has to as well."""
        r = _simulate_sbom_chain(
            job_status="failure",
            build_outcome="success",
            downloads=("failure", "success", "success"),
        )
        assert r[_WAIT_NAMES[0]] == "success", "the wait must run under a red job"
        assert r["syft-dl-2"] == "success", (
            "attempt 2 must run under a red job — dropping the !cancelled() "
            "guard here silently disables retrying exactly when a CVE is present"
        )
        assert r["sbom-upload"] == "success"

    def test_scan_failed_operationally_chain_still_runs(self) -> None:
        """Deliberate divergence from the SARIF steps: the SBOM reads the built
        image, not trivy-image.json, so an operational Trivy failure must not
        take the inventory down with it."""
        job = _build_image_and_scan_job()
        for step_id in (*_DOWNLOAD_IDS, "syft-cmd", "sbom-generate", "sbom-require"):
            cond = _step_by_id(job, step_id).get("if", "success()")
            assert "trivy" not in cond, (
                f"{step_id} must not depend on any trivy step — the SBOM does "
                "not read trivy-image.json"
            )
        r = _simulate_sbom_chain(job_status="failure", build_outcome="success")
        assert r["sbom-upload"] == "success"

    def test_build_failed_whole_chain_skipped(self) -> None:
        r = _simulate_sbom_chain(job_status="failure", build_outcome="failure")
        assert all(v == "skipped" for v in r.values()), (
            f"nothing may run against an image that failed to build: {r}"
        )

    def test_build_skipped_whole_chain_skipped(self) -> None:
        """Kills `!cancelled() && steps.build-image.outcome != 'failure'`: a
        `skipped` build outcome is `!= 'failure'`, so that wrong condition would
        run syft against an image that was never built at all."""
        r = _simulate_sbom_chain(job_status="failure", build_outcome="skipped")
        assert all(v == "skipped" for v in r.values()), (
            f"nothing may run against an image that was never built: {r}"
        )


class TestSbomRetryChain:
    """Plan 309 — the retry, and the two mechanics that make it real rather
    than decorative. Every scenario below was also forced against real CI; the
    run ids are named so a future reader can check the simulation against it."""

    def test_a_failed_attempt_triggers_the_next_one(self) -> None:
        """Real-CI counterpart: run 35620778764 (attempt 1 pinned to a bogus
        version, attempt 2 real)."""
        r = _simulate_sbom_chain(
            job_status="success",
            build_outcome="success",
            downloads=("failure", "success", "success"),
        )
        assert r[_WAIT_NAMES[0]] == "success"
        assert r["syft-dl-2"] == "success"
        assert r[_WAIT_NAMES[1]] == "skipped", "no wait after a successful retry"
        assert r["syft-dl-3"] == "skipped"
        assert r["sbom-upload"] == "success", (
            "a recovered download must still produce an uploaded SBOM"
        )

    def test_all_attempts_failing_still_reaches_enforcement(self) -> None:
        """🔑 The failure that must stay loud. Real-CI counterpart: run
        35621394667 — job red, with an annotation naming the download stage."""
        r = _simulate_sbom_chain(
            job_status="success",
            build_outcome="success",
            downloads=("failure", "failure", "failure"),
        )
        assert r["syft-dl-3"] == "failure"
        assert r["sbom-generate"] == "skipped", (
            "the scan must not run without a syft binary"
        )
        assert r["sbom-require"] == "success" or r["sbom-require"] == "failure", (
            "the enforcement step must RUN — if it is skipped, a missing SBOM "
            "passes silently, which is the whole defect"
        )
        assert r["sbom-require"] != "skipped"
        assert r["sbom-upload"] == "skipped"

    def test_upload_is_gated_on_enforcement_not_on_the_scan(self) -> None:
        """🔴 Found by forced-fault run 35622426919, not by review. syft can
        exit 0 having written nothing: `sbom-generate.outcome == 'success'`
        while no file exists. Gating the upload on the scan made it run and fail
        on `if-no-files-found: error`, adding a confusing second error beside the
        real one — the exact thing ci.yml's own comment warns against."""
        job = _build_image_and_scan_job()
        cond = _step_by_id(job, "sbom-upload")["if"]
        assert "sbom-require" in cond, (
            "the upload must be gated on the enforcement step, not the scan"
        )
        r = _simulate_sbom_chain(
            job_status="success", build_outcome="success", artifact_valid=False
        )
        assert r["sbom-generate"] == "success", "the scan itself succeeded"
        assert r["sbom-require"] == "failure"
        assert r["sbom-upload"] == "skipped", (
            "no artifact, no upload — and no second confusing error"
        )

    def test_enforcement_is_the_only_step_that_can_fail_the_job(self) -> None:
        job = _build_image_and_scan_job()
        for step_id in (*_DOWNLOAD_IDS, "sbom-generate"):
            assert _step_by_id(job, step_id).get("continue-on-error") is True, (
                f"{step_id} must be continue-on-error, or the job dies before "
                "the enforcement step can classify and report the failure"
            )
        require = _step_by_id(job, "sbom-require")
        assert "continue-on-error" not in require, (
            "the enforcement step must NOT be continue-on-error — it is the "
            "single explicit decision that keeps Plan 180's property"
        )

    def test_every_attempt_and_the_scan_are_individually_bounded(self) -> None:
        """A job-level timeout CANCELS, which skips every `!cancelled()` step —
        no enforcement, no message, no upload. So the bound cannot live only at
        the job level."""
        job = _build_image_and_scan_job()
        for step_id in (*_DOWNLOAD_IDS, "sbom-generate"):
            step = _step_by_id(job, step_id)
            assert isinstance(step.get("timeout-minutes"), int), (
                f"{step_id} needs its own timeout-minutes — one stalled step "
                "otherwise consumes the job budget and the job is cancelled"
            )

    def test_the_scan_keeps_the_parent_actions_update_check_disabled(self) -> None:
        """`anchore/sbom-action` sets SYFT_CHECK_FOR_APP_UPDATE=false itself
        (src/github/SyftGithubAction.ts:127); the download-syft sub-action does
        not. Omitting it adds an outbound update check to the step whose entire
        problem is outbound network calls."""
        env = _step_by_id(_build_image_and_scan_job(), "sbom-generate").get("env", {})
        assert env.get("SYFT_CHECK_FOR_APP_UPDATE") == "false"

    def test_all_three_attempts_share_one_pinned_action(self) -> None:
        job = _build_image_and_scan_job()
        pins = {_step_by_id(job, sid)["uses"] for sid in _DOWNLOAD_IDS}
        assert len(pins) == 1, f"the attempts must share one pin, found: {pins}"
        assert re.fullmatch(
            r"anchore/sbom-action/download-syft@[0-9a-f]{40}", pins.pop()
        ), "the download action must be pinned to a full commit SHA"


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


class TestTheHelperModelsImplicitSuccess:
    """Review blocker (Plan 180): the `if:` evaluator must reproduce GitHub's
    implicit `success()`, or a condition that drops its status function passes
    here while real Actions skips the step after the gate fails — reintroducing
    the invisible failure this plan exists to close."""

    def test_a_condition_without_a_status_function_is_skipped_after_a_failure(
        self,
    ) -> None:
        # The exact counterexample from the review: looks correct, is not.
        assert not _eval_gha_if(
            "${{ steps.trivy-scan.outcome == 'success' }}",
            job_status="failure",
            outcomes={"trivy-scan": "success"},
        )

    def test_the_same_condition_still_runs_while_the_job_is_healthy(self) -> None:
        assert _eval_gha_if(
            "${{ steps.trivy-scan.outcome == 'success' }}",
            job_status="success",
            outcomes={"trivy-scan": "success"},
        )

    def test_an_explicit_status_function_suppresses_the_implicit_gate(self) -> None:
        """What the shipped workflow actually uses — `!cancelled()` keeps the step
        running after the gate fails, which is the whole point."""
        assert _eval_gha_if(
            "${{ !cancelled() && steps.trivy-scan.outcome == 'success' }}",
            job_status="failure",
            outcomes={"trivy-scan": "success"},
        )


class TestEveryNetworkStepIsBounded:
    """Plan 309 T2 (iii) — the convention, encoded so it cannot quietly erode.

    A **job**-level timeout CANCELS the job, which skips every `!cancelled()`
    step: no enforcement, no failure message, no upload. So a step that reaches
    the network needs its OWN bound, or one hung fetch turns a red check that
    explains itself into a bare cancellation.

    Measured on the healthy run 35625551801: every network fetch completed in
    ≤ 40 s except `Build app image` at 124 s. Bounds are 3–8 minutes; the
    tightest ratio is `Build app image` at 480/124 ≈ **3.9x** and the loosest is
    `actions/checkout` at 180/3 = 60x. *(An earlier docstring claimed "7x–30x",
    which was wrong at both ends — independent review 2026-09-22.)* These assert
    that a bound EXISTS, not that it is tight.

    ⚠️ Deliberately NOT applied to the pytest steps: they are the work, not a
    fetch, and the job timeout is their correct bound.
    """

    _NETWORK_USES = (
        "actions/checkout@",
        "astral-sh/setup-uv@",
        "docker/setup-buildx-action@",
        "docker/build-push-action@",
        "actions/upload-artifact@",
        "github/codeql-action/upload-sarif@",
        "aquasecurity/trivy-action@",
        "anchore/sbom-action",
    )
    # 🔴 `gh api` was missing, and the count-based guard below could not see the
    # omission — a whole CATEGORY can vanish while the count still passes
    # (independent review 2026-09-22, medium). The category assertions in
    # `test_every_network_category_is_represented` are what actually guard it.
    _NETWORK_RUN = ("apt-get", "uv sync", "pip install", "gh api", "curl ", "wget ")

    def _network_steps(self) -> list[tuple[str, str, dict[str, Any]]]:
        workflow = yaml.safe_load(_ci_yml_text())
        found = []
        for job_name, job in workflow["jobs"].items():
            for step in job.get("steps") or []:
                uses = str(step.get("uses", ""))
                run = str(step.get("run", ""))
                # ⚠️ Only skip a step that is PURELY pytest. A combined
                # install-and-test script still fetches, and skipping it on the
                # substring alone would hide it (independent review 2026-09-22).
                if "pytest" in run and not any(tok in run for tok in self._NETWORK_RUN):
                    continue
                hit = any(uses.startswith(u) for u in self._NETWORK_USES) or any(
                    token in run for token in self._NETWORK_RUN
                )
                if hit:
                    label = step.get("name") or uses.split("@")[0] or run.strip()[:40]
                    found.append((job_name, label, step))
        return found

    def test_the_survey_still_finds_network_steps(self) -> None:
        """Guards the test itself: if the selectors stopped matching, every
        assertion below would pass vacuously."""
        steps = self._network_steps()

        assert len(steps) >= 15, f"expected the whole fetch surface, found {len(steps)}"

    def test_every_network_category_is_represented(self) -> None:
        """🔴 A count cannot detect a missing CATEGORY. The `gh api` steps were
        absent from the selector list and the count still passed — which is how
        an unbounded network step survived the first version of this convention
        (independent review 2026-09-22, medium).

        Each entry below is a distinct third party that can be down on its own.
        """
        found = self._network_steps()
        blob = " ".join(
            str(s.get("uses", "")) + " " + str(s.get("run", "")) for _, _, s in found
        )
        for category in (
            "actions/checkout",  # github.com git
            "astral-sh/setup-uv",  # the uv binary
            "docker/build-push",  # Docker Hub
            "aquasecurity/trivy",  # trivy binary + vulnerability DB
            "anchore/sbom-action",  # the syft release
            "apt-get",  # Debian/Ubuntu mirrors
            "uv sync",  # PyPI + the private git repos
            "gh api",  # the GitHub REST API
        ):
            assert category in blob, (
                f"no network step matched {category!r} — either it was removed "
                "from the workflow, or the selector stopped seeing a whole "
                "category and the bound-check below is now blind to it"
            )

    def test_no_jobs_bounded_steps_alone_exceed_its_own_timeout(self) -> None:
        """Bounding each step does not bound their SUM: `build-image-and-scan`
        summed to **69 minutes against a 30-minute job**, so it would cancel
        mid-retry and skip the enforcement steps that exist to explain the
        failure (independent review 2026-09-22, medium).

        ⛔ **This is a NECESSARY condition, not a sufficient one, and the
        difference matters.** *(Second review pass, medium: the earlier version
        of this test was named `..._can_actually_reach_its_enforcement_steps`
        and claimed exactly the guarantee it cannot give.)* It counts unbounded
        work as **zero** — the `unit` job's pytest step alone runs ~11 minutes
        and is invisible here, and the image job's smoke check and both
        `trivy convert` calls are unbounded too. A hang in any of those still
        consumes the whole job budget.

        🔑 **The actual guarantee needs elapsed-time admission before each
        retry, which is [Plan 310].** This test only catches the arithmetic
        absurdity that was really there.

        Mutually exclusive steps are excluded (only one `Install (...)` variant
        runs), and sleeps are counted only inside steps that are NOT themselves
        bounded — otherwise an `apt` step's internal retry sleeps are counted
        twice, once in its own bound and once again here.
        """
        workflow = yaml.safe_load(_ci_yml_text())
        over = []
        for job_name, job in workflow["jobs"].items():
            job_timeout = job.get("timeout-minutes")
            if job_timeout is None:
                continue
            steps = job.get("steps") or []
            # Only one of the mutually exclusive `Install (...)` variants can run.
            install_variants = [
                s for s in steps if str(s.get("name", "")).startswith("Install (")
            ]
            skip = set()
            if len(install_variants) > 1:
                cheapest = min(
                    install_variants, key=lambda s: s.get("timeout-minutes") or 0
                )
                skip.add(id(cheapest))

            bounded = sum(
                s.get("timeout-minutes") or 0 for s in steps if id(s) not in skip
            )
            # A bounded step's own internal sleeps are already inside its bound.
            sleeps = sum(
                int(m) / 60
                for s in steps
                if s.get("timeout-minutes") is None and id(s) not in skip
                for m in re.findall(r"\bsleep\s+(\d+)", str(s.get("run", "")))
            )
            worst = bounded + sleeps
            if worst > job_timeout:
                over.append(
                    f"{job_name}: bounded {bounded}m + unbounded sleeps "
                    f"{sleeps:.0f}m = {worst:.0f}m > job timeout {job_timeout}m"
                )

        assert over == [], (
            "a job's own bounded steps cannot fit inside its timeout, so it "
            "would cancel mid-chain: " + "; ".join(over)
        )

    def test_the_trivy_report_gate_is_not_forgiving(self) -> None:
        """Mirrors the SBOM enforcement rule. `Require a Trivy report` is the one
        step in that chain that decides, so it must not be continue-on-error —
        otherwise both attempts can fail and the job still goes green."""
        step = _step_by_id(_build_image_and_scan_job(), "trivy-report")

        assert "continue-on-error" not in step, (
            "the Trivy enforcement step must NOT be continue-on-error — it is "
            "the single explicit decision that keeps a missing report loud"
        )


class TestTheTrivyReportCheckAcceptsACleanReport:
    """🔴 A zero-result trivy report must be ACCEPTED, not called a download
    failure.

    `Results` carries `omitempty` in trivy's report type, so a report with no
    result entries can omit the key entirely. An earlier version of this check
    used `jq -e '.Results'`, which is a TRUTHINESS test: it rejects an absent or
    null `Results` and would have labelled a legitimate clean scan a download
    failure (independent review 2026-09-22, medium).

    These run the filter **extracted from the workflow itself**, so the test
    cannot drift away from what CI actually executes.
    """

    @staticmethod
    def _filter_from_workflow() -> str:
        step = _step_by_id(_build_image_and_scan_job(), "trivy-report")
        m = re.search(r"jq -e '([^']+)' trivy-image\.json", step["run"])
        assert m, "could not find the report-validity jq filter in the step"
        return m.group(1)

    @staticmethod
    def _accepts(jq_filter: str, document: str) -> bool:
        proc = subprocess.run(
            ["jq", "-e", jq_filter],
            input=document,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0

    def test_a_clean_report_with_no_results_is_accepted(self) -> None:
        """The case the old filter got wrong."""
        clean = json.dumps(
            {
                "SchemaVersion": 2,
                "ArtifactName": "sapphire-flow:ci-abc",
                "ArtifactType": "container_image",
                "Metadata": {"OS": {"Family": "debian"}},
            }
        )

        assert self._accepts(self._filter_from_workflow(), clean), (
            "a trivy report with no Results key is a CLEAN SCAN, not a download "
            "failure — `Results` is omitempty"
        )

    def test_a_report_with_an_empty_results_array_is_accepted(self) -> None:
        clean = json.dumps({"SchemaVersion": 2, "Results": []})

        assert self._accepts(self._filter_from_workflow(), clean)

    def test_a_report_with_findings_is_accepted(self) -> None:
        found = json.dumps(
            {
                "SchemaVersion": 2,
                "Results": [
                    {"Target": "x", "Vulnerabilities": [{"VulnerabilityID": "CVE-1"}]}
                ],
            }
        )

        assert self._accepts(self._filter_from_workflow(), found)

    def test_a_non_report_is_rejected(self) -> None:
        """The check must still catch the thing it exists for."""
        jq_filter = self._filter_from_workflow()

        assert not self._accepts(jq_filter, "{}"), "an empty object is not a report"
        assert not self._accepts(jq_filter, "[]"), "an array is not a report"
        assert not self._accepts(jq_filter, '"nope"'), "a string is not a report"

    def test_a_structurally_invalid_envelope_is_rejected(self) -> None:
        """🔴 Key PRESENCE is not validity. `{"SchemaVersion": "garbage"}`
        satisfies `has("SchemaVersion")` and then fails downstream conversion —
        and every fixture above accepted that defective validator, because none
        supplied an object with a bad envelope (independent review 2026-09-22,
        medium). These are the cases that distinguish the two predicates.
        """
        jq_filter = self._filter_from_workflow()

        assert not self._accepts(
            jq_filter, json.dumps({"SchemaVersion": "garbage", "Results": []})
        ), "SchemaVersion must be a NUMBER, not merely present"
        assert not self._accepts(
            jq_filter, json.dumps({"SchemaVersion": None, "Results": []})
        ), "a null SchemaVersion is not a version"
        assert not self._accepts(
            jq_filter, json.dumps({"SchemaVersion": 2, "Results": "not-a-list"})
        ), "Results, when present, must be an array"
        assert self._accepts(jq_filter, json.dumps({"SchemaVersion": 2})), (
            "but an ABSENT Results is a clean scan and must still be accepted"
        )
