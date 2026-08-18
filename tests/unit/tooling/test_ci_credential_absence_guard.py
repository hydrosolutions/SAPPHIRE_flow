"""Plan 185 — CI credential-absence guard.

Structural assertions over the parsed ``unit`` job in ``ci.yml`` (steps
selected by ``name``, never by regex over the raw text — see
``test_trivy_gate_observability.py``) plus prose assertions on
``docs/standards/cicd.md``. In the manner of ``test_recap_wheel_guard.py``
(Plan 082 Task 2H): a single selector each, not a bespoke evaluator for the
`if:` boolean expressions — Plan 185's own proportionality note explicitly
rejects a Python re-implementation of GitHub Actions expression semantics as
disproportionate to a two-branch conditional.

Three outcomes (D1):
1. Token present -> install the extra (unchanged).
2. Token absent, Dependabot PR, uv.lock NOT touched -> plain sync + warning.
3. Token absent, anything else (including a Dependabot PR that DOES touch
   uv.lock) -> fail, naming AQUACAST_TOKEN.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_JOB_NAME = "unit"


def _ci_yml() -> dict[str, Any]:
    text = (_REPO_ROOT / ".github/workflows/ci.yml").read_text()
    return yaml.safe_load(text)


def _cicd_md_text() -> str:
    return (_REPO_ROOT / "docs/standards/cicd.md").read_text()


def _unit_job() -> dict[str, Any]:
    return _ci_yml()["jobs"][_JOB_NAME]


def _step(name: str) -> dict[str, Any]:
    for step in _unit_job()["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r} in the {_JOB_NAME} job")


class TestTokenPresenceReadFromJobLevelEnv:
    """D2 — presence read from a job-level env value, not a detect step."""

    def test_job_level_env_declares_token_presence(self) -> None:
        job = _unit_job()
        assert job.get("env", {}).get("AQUACAST_TOKEN_PRESENT") == (
            "${{ secrets.AQUACAST_TOKEN != '' }}"
        )

    def test_no_step_if_references_secrets_aquacast_token_directly(self) -> None:
        # Only the job-level env: block may read secrets.AQUACAST_TOKEN; every
        # step-level `if:` must read env.AQUACAST_TOKEN_PRESENT instead, since
        # `if:` cannot reference `secrets.*` directly.
        for step in _unit_job()["steps"]:
            condition = step.get("if", "")
            assert "secrets.AQUACAST_TOKEN" not in condition


class TestJobPermissions:
    """D6 — pinned permissions block; contents: read must be restated."""

    def test_job_restates_contents_read(self) -> None:
        assert _unit_job().get("permissions", {}).get("contents") == "read"

    def test_job_grants_pull_requests_read(self) -> None:
        assert _unit_job().get("permissions", {}).get("pull-requests") == "read"


class TestLockChangeDetection:
    """D6 — exact uv.lock diff via `gh pr diff`, guarded to the one case that
    needs it; fails closed on an API failure."""

    def test_detect_step_exists_and_is_guarded_to_dependabot_pull_requests(
        self,
    ) -> None:
        step = _step("Detect uv.lock change (Dependabot degraded-coverage guard)")
        condition = step["if"]
        assert "env.AQUACAST_TOKEN_PRESENT != 'true'" in condition
        assert "github.event_name == 'pull_request'" in condition
        assert "github.event.pull_request.user.login == 'dependabot[bot]'" in condition

    def test_detect_step_uses_gh_token_env(self) -> None:
        step = _step("Detect uv.lock change (Dependabot degraded-coverage guard)")
        assert step["env"]["GH_TOKEN"] == "${{ github.token }}"

    def test_detect_step_reads_pr_diff_name_only(self) -> None:
        step = _step("Detect uv.lock change (Dependabot degraded-coverage guard)")
        assert (
            'gh pr diff "${{ github.event.pull_request.number }}" --name-only'
            in step["run"]
        )

    def test_detect_step_matches_uv_lock_exactly_not_a_substring(self) -> None:
        step = _step("Detect uv.lock change (Dependabot degraded-coverage guard)")
        assert "grep -qxF 'uv.lock'" in step["run"]

    def test_detect_step_fails_closed_on_gh_failure(self) -> None:
        step = _step("Detect uv.lock change (Dependabot degraded-coverage guard)")
        run = step["run"]
        # the success/failure branch of the `gh pr diff` command must be
        # tested BEFORE its output is trusted (an `if gh ...; then` guard),
        # and the else branch must set UV_LOCK_CHANGED=true (fail closed).
        assert "if gh pr diff" in run
        else_clause = run.split("else", 1)[1]
        assert "UV_LOCK_CHANGED=true" in else_clause

    def test_detect_step_never_runs_on_push(self) -> None:
        # push events have no github.event.pull_request.number; the detect
        # step must require pull_request explicitly, not merely token-absent.
        step = _step("Detect uv.lock change (Dependabot degraded-coverage guard)")
        assert "github.event_name == 'pull_request'" in step["if"]


class TestFailCase:
    """D1 case 3 — token absent, and not the one degrade-eligible case."""

    def test_fail_step_condition_excludes_only_the_degrade_eligible_case(
        self,
    ) -> None:
        step = _step("AQUACAST_TOKEN absent — fail")
        condition = step["if"]
        assert "env.AQUACAST_TOKEN_PRESENT != 'true'" in condition
        assert "dependabot[bot]" in condition
        assert "env.UV_LOCK_CHANGED == 'false'" in condition

    def test_fail_step_names_the_token_and_exits_nonzero(self) -> None:
        step = _step("AQUACAST_TOKEN absent — fail")
        run = step["run"]
        assert "::error::" in run
        assert "AQUACAST_TOKEN" in run
        assert "exit 1" in run


class TestInstalledCase:
    """D1 case 1 — token present, unchanged behaviour."""

    def test_install_step_still_uses_extra_aquacast(self) -> None:
        step = _step("Install (aquacast extra)")
        assert step["run"] == "uv sync --frozen --extra aquacast"

    def test_install_step_gated_on_token_present(self) -> None:
        step = _step("Install (aquacast extra)")
        assert step["if"] == "env.AQUACAST_TOKEN_PRESENT == 'true'"


class TestDegradedCase:
    """D1 case 2 + D3 — plain sync, visible ::warning:: annotation."""

    def test_degraded_step_condition_requires_dependabot_and_untouched_lock(
        self,
    ) -> None:
        step = _step("Install (no aquacast extra — degraded)")
        condition = step["if"]
        assert "env.AQUACAST_TOKEN_PRESENT != 'true'" in condition
        assert "dependabot[bot]" in condition
        assert "env.UV_LOCK_CHANGED == 'false'" in condition

    def test_degraded_step_still_syncs_the_base_project(self) -> None:
        step = _step("Install (no aquacast extra — degraded)")
        assert "uv sync --frozen" in step["run"]
        assert "--extra aquacast" not in step["run"]

    def test_degraded_step_emits_a_warning_annotation_naming_the_lost_coverage(
        self,
    ) -> None:
        step = _step("Install (no aquacast extra — degraded)")
        run = step["run"]
        assert "::warning::" in run
        assert "test_aquacast_shim.py" in run


class TestD4ProvesTheShimTestRan:
    """D4 — exit status alone is insufficient; assert on pytest's own
    summary line, on the one node that genuinely requires the package."""

    def test_assertion_step_gated_on_token_present(self) -> None:
        step = _step("Prove the aquacast shim test ran")
        assert step["if"] == "env.AQUACAST_TOKEN_PRESENT == 'true'"

    def test_assertion_step_sets_pipefail(self) -> None:
        step = _step("Prove the aquacast shim test ran")
        assert "set -o pipefail" in step["run"]

    def test_assertion_step_targets_the_single_required_node(self) -> None:
        step = _step("Prove the aquacast shim test ran")
        assert (
            "tests/unit/models/test_aquacast_shim.py::TestRealDiscovery::"
            "test_discover_models_returns_the_aquacast_model" in step["run"]
        )

    def test_assertion_step_greps_for_1_passed_not_just_exit_status(self) -> None:
        step = _step("Prove the aquacast shim test ran")
        assert "grep -Fq '1 passed'" in step["run"]

    def test_pipefail_precedes_the_piped_pytest_invocation(self) -> None:
        step = _step("Prove the aquacast shim test ran")
        run = step["run"]
        pipefail_idx = run.index("set -o pipefail")
        pytest_idx = run.index("uv run pytest")
        assert pipefail_idx < pytest_idx


class TestStepOrdering:
    def test_detect_precedes_fail_precedes_installs_precedes_assertion(
        self,
    ) -> None:
        names = [s.get("name") for s in _unit_job()["steps"]]
        detect = names.index(
            "Detect uv.lock change (Dependabot degraded-coverage guard)"
        )
        fail = names.index("AQUACAST_TOKEN absent — fail")
        install = names.index("Install (aquacast extra)")
        degraded = names.index("Install (no aquacast extra — degraded)")
        prove = names.index("Prove the aquacast shim test ran")
        assert detect < fail < install
        assert detect < degraded
        assert install < prove


class TestFinalUnitTestStepUnchanged:
    def test_last_step_still_runs_the_full_unit_suite(self) -> None:
        last = _unit_job()["steps"][-1]
        assert last.get("run") == (
            "uv run pytest tests/unit/ --cov=src/sapphire_flow "
            "--cov-report=term-missing -v"
        )


class TestCicdMdDocumentsBothSecretStores:
    """T2 — the runbook paragraph."""

    def test_states_the_token_must_be_mirrored_into_both_stores(self) -> None:
        text = _cicd_md_text()
        assert "AQUACAST_TOKEN" in text
        assert "Dependabot" in text

    def test_names_recap_dg_client_token_as_the_precedent(self) -> None:
        text = _cicd_md_text()
        assert "RECAP_DG_CLIENT_TOKEN" in text
