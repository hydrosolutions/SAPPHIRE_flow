"""Plan 185 — CI credential-absence guard.

Structural assertions over the parsed ``unit`` job in ``ci.yml`` (steps
selected by ``name``, never by regex over the raw text — see
``test_trivy_gate_observability.py``) plus prose assertions on
``docs/standards/cicd.md``. In the manner of ``test_recap_wheel_guard.py``
(Plan 082 Task 2H): one guard, one file, structural selectors — not a
bespoke evaluator for the `if:` boolean expressions. Where an `if:`
condition is itself the safety property under test (D1 case 3's fail gate,
the Dependabot-actor guards), assertions compare the FULL normalized
condition string, not substrings — a substring check passes even after the
condition's `!`/`&&`/`||` structure is broken.

Three outcomes (D1):
1. Token present -> install the extra (unchanged).
2. Token absent, run triggered by the Dependabot actor, uv.lock NOT touched
   -> plain sync + warning.
3. Token absent, anything else (including such a run whose PR DOES touch
   uv.lock) -> fail, naming AQUACAST_TOKEN.

D6 (fixer round): "Dependabot-triggered" is decided by `github.actor` (who
triggered THIS run), never `github.event.pull_request.user.login` (the PR's
original author) — a maintainer pushing a further commit onto a
Dependabot-opened branch runs as the maintainer, and the author-based
predicate would misclassify that human-triggered run as degrade-eligible.
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


def _cicd_md_new_section() -> str:
    """Just the Plan 185 runbook paragraph, not the whole document — a
    passing assertion here must mean this specific section says the thing,
    not that the word appears somewhere else on the page."""
    marker = "### Private dependency credentials — both secret stores (Plan 185)"
    text = _cicd_md_text()
    start = text.index(marker)
    rest = text[start:]
    next_heading = rest.find("\n### ", 1)
    return rest if next_heading == -1 else rest[:next_heading]


def _unit_job() -> dict[str, Any]:
    return _ci_yml()["jobs"][_JOB_NAME]


def _step(name: str) -> dict[str, Any]:
    for step in _unit_job()["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r} in the {_JOB_NAME} job")


def _normalized_if(name: str) -> str:
    """The step's `if:` with YAML folding/indentation whitespace collapsed,
    so equality checks pin the boolean structure (`!`, `&&`, `||`, operand
    order) without being sensitive to incidental line-wrapping."""
    return " ".join(_step(name)["if"].split())


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

    def test_job_permissions_are_exactly_contents_and_pull_requests_read(
        self,
    ) -> None:
        assert _unit_job().get("permissions") == {
            "contents": "read",
            "pull-requests": "read",
        }


class TestActorNotAuthorDecidesDependabotAttribution:
    """D6 fixer round — GitHub withholds secrets by who triggered THIS run
    (`github.actor`), not by the PR's original author
    (`github.event.pull_request.user.login`), which stays 'dependabot[bot]'
    even after a maintainer pushes a further commit to the branch."""

    def test_no_condition_in_the_unit_job_reads_pull_request_user_login(
        self,
    ) -> None:
        for step in _unit_job()["steps"]:
            condition = step.get("if", "")
            assert "user.login" not in condition, (
                f"step {step.get('name')!r} still keys Dependabot "
                "attribution off the PR author, not github.actor"
            )

    def test_exactly_the_three_dependabot_gated_steps_read_github_actor(
        self,
    ) -> None:
        gated = {
            step["name"]
            for step in _unit_job()["steps"]
            if "dependabot[bot]" in step.get("if", "")
        }
        assert gated == {
            "Detect uv.lock change (Dependabot degraded-coverage guard)",
            "AQUACAST_TOKEN absent — fail",
            "Install (no aquacast extra — degraded)",
        }
        for name in gated:
            assert "github.actor == 'dependabot[bot]'" in _step(name)["if"]


class TestLockChangeDetection:
    """D6 — exact uv.lock touch via the paginated pull-request-files API,
    guarded to the one case that needs it; fails closed on an API failure."""

    def test_detect_step_condition_matches_exactly(self) -> None:
        assert _normalized_if(
            "Detect uv.lock change (Dependabot degraded-coverage guard)"
        ) == (
            "env.AQUACAST_TOKEN_PRESENT != 'true' && "
            "github.event_name == 'pull_request' && "
            "github.actor == 'dependabot[bot]'"
        )

    def test_detect_step_uses_gh_token_env(self) -> None:
        step = _step("Detect uv.lock change (Dependabot degraded-coverage guard)")
        assert step["env"]["GH_TOKEN"] == "${{ github.token }}"

    def test_detect_step_reads_the_paginated_files_api_not_a_capped_diff(
        self,
    ) -> None:
        step = _step("Detect uv.lock change (Dependabot degraded-coverage guard)")
        run = step["run"]
        # `gh pr diff --name-only` only ever shows a rename's post-rename
        # path (a rename-away from uv.lock is invisible to it) and GitHub
        # truncates a PR diff past 300 changed files. The paginated
        # This avoids the diff API's 300-file truncation; the separate
        # 3000-file files-endpoint cap is guarded in the step itself.
        assert "gh api" in run
        assert "--paginate" in run
        assert "/pulls/" in run and "/files" in run
        assert "gh pr diff" not in run
        assert "--name-only" not in run

    def test_detect_step_checks_both_filename_and_previous_filename(self) -> None:
        step = _step("Detect uv.lock change (Dependabot degraded-coverage guard)")
        run = step["run"]
        assert ".filename" in run
        assert ".previous_filename" in run

    def test_detect_step_matches_uv_lock_exactly_not_a_substring(self) -> None:
        step = _step("Detect uv.lock change (Dependabot degraded-coverage guard)")
        assert "grep -qxF 'uv.lock'" in step["run"]

    def test_detect_step_fails_closed_on_gh_failure(self) -> None:
        step = _step("Detect uv.lock change (Dependabot degraded-coverage guard)")
        run = step["run"]
        # the success/failure branch of the paginated `gh api .../files` call
        # must be tested BEFORE its output is trusted (an `elif gh ...; then`
        # guard), and the final else branch must set UV_LOCK_CHANGED=true
        # (fail closed).
        assert "elif gh api --paginate" in run
        final_else = run.rsplit("\nelse\n", 1)[1]
        assert "UV_LOCK_CHANGED=true" in final_else

    def test_detect_step_reads_changed_files_count_before_paginating(self) -> None:
        # The pull-request-files endpoint has a hard 3000-file cap that
        # `--paginate` cannot lift; the count must be read (and checked)
        # BEFORE the possibly-incomplete paginated listing is trusted.
        step = _step("Detect uv.lock change (Dependabot degraded-coverage guard)")
        run = step["run"]
        assert "count=$(gh api" in run
        assert ".changed_files" in run
        count_idx = run.index(".changed_files")
        paginate_call_idx = run.index("gh api --paginate")
        assert count_idx < paginate_call_idx

    def test_detect_step_fails_closed_on_an_unusable_changed_file_count(self) -> None:
        """Empty AND non-numeric counts must fail closed.

        `[ "$count" -gt 3000 ]` exits 2 on a non-number, and `bash -e` does not
        abort on a failed `elif` CONDITION — so a malformed count (jq yields
        "null" when the field is absent) would otherwise fall through to the file
        listing and could set UV_LOCK_CHANGED=false, silently allowing degraded
        coverage on a PR whose lock status was never established.
        """
        step = _step("Detect uv.lock change (Dependabot degraded-coverage guard)")
        run = step["run"]
        assert '|| count=""' in run
        guard = 'if ! [[ "$count" =~ ^[0-9]+$ ]]; then'
        assert guard in run, "the count guard must reject non-numeric, not just empty"
        assert 'if [ -z "$count" ]; then' not in run, (
            "an emptiness-only check lets a non-numeric count fail OPEN"
        )
        count_fail_branch = run.split(guard, 1)[1].split("elif", 1)[0]
        assert "UV_LOCK_CHANGED=true" in count_fail_branch

    def test_detect_step_fails_closed_when_changed_files_exceeds_3000_cap(
        self,
    ) -> None:
        # `--paginate` does not remove the endpoint's own hard 3000-file
        # maximum: a PR that touches more files than that could have
        # uv.lock outside the returned page set, so the guard must not
        # trust the listing past that cap.
        step = _step("Detect uv.lock change (Dependabot degraded-coverage guard)")
        run = step["run"]
        assert 'elif [ "$count" -gt 3000 ]; then' in run
        cap_branch = run.split('elif [ "$count" -gt 3000 ]; then', 1)[1].split(
            "elif gh api --paginate", 1
        )[0]
        assert "UV_LOCK_CHANGED=true" in cap_branch

    def test_detect_step_grep_match_maps_to_true_no_match_maps_to_false(
        self,
    ) -> None:
        # Locks the branch-to-value mapping itself: grep finding `uv.lock`
        # must set true, the no-match branch must set false. Flipping either
        # assignment must fail this test.
        step = _step("Detect uv.lock change (Dependabot degraded-coverage guard)")
        run = step["run"]
        grep_idx = run.index("if grep -qxF 'uv.lock'")
        inner = run[grep_idx:]
        then_idx = inner.index("then")
        else_idx = inner.index("\n  else\n")
        fi_idx = inner.index("\n  fi\n")
        then_branch = inner[then_idx:else_idx]
        else_branch = inner[else_idx:fi_idx]
        assert "UV_LOCK_CHANGED=true" in then_branch
        assert "UV_LOCK_CHANGED=false" not in then_branch
        assert "UV_LOCK_CHANGED=false" in else_branch
        assert "UV_LOCK_CHANGED=true" not in else_branch

    def test_only_the_detect_step_ever_sets_uv_lock_changed(self) -> None:
        # If any later step could also write UV_LOCK_CHANGED, the fail
        # step's "fails closed on API failure" guarantee could be
        # overwritten before it is read.
        setters = [
            step["name"]
            for step in _unit_job()["steps"]
            if "UV_LOCK_CHANGED=" in step.get("run", "")
        ]
        assert setters == ["Detect uv.lock change (Dependabot degraded-coverage guard)"]


class TestFailCase:
    """D1 case 3 — token absent, and not the one degrade-eligible case.
    The single most safety-critical condition in this guard: its exact
    structure (leading `!`, the `&&`-wrapped negated clause) is what makes
    "must fail" and "safe to degrade" mutually exclusive."""

    def test_fail_step_condition_matches_exactly(self) -> None:
        assert _normalized_if("AQUACAST_TOKEN absent — fail") == (
            "env.AQUACAST_TOKEN_PRESENT != 'true' && "
            "!(github.event_name == 'pull_request' && "
            "github.actor == 'dependabot[bot]' && "
            "env.UV_LOCK_CHANGED == 'false')"
        )

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
    """D1 case 2 + D3 — plain sync, visible ::warning:: annotation. The
    condition is the exact negation-free counterpart of the fail step's:
    both must partition the "token absent" space with no overlap and no gap."""

    def test_degraded_step_condition_matches_exactly(self) -> None:
        assert _normalized_if("Install (no aquacast extra — degraded)") == (
            "env.AQUACAST_TOKEN_PRESENT != 'true' && "
            "github.event_name == 'pull_request' && "
            "github.actor == 'dependabot[bot]' && "
            "env.UV_LOCK_CHANGED == 'false'"
        )

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
    """T2 — the runbook paragraph, scoped to the new section (not the whole
    document, where these words could appear unrelated to this guard)."""

    def test_new_section_names_both_stores_by_name(self) -> None:
        section = _cicd_md_new_section()
        assert "AQUACAST_TOKEN" in section
        assert "Dependabot" in section
        assert "Actions" in section
        assert "both" in section.lower()
