from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "ci_attest_test", ROOT / "tools/ci_attest.py"
)
assert spec is not None and spec.loader is not None
attest = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = attest
spec.loader.exec_module(attest)


@pytest.fixture
def full_jobs() -> list[dict]:
    return [
        {
            "id": index,
            "run_id": 1,
            "run_attempt": 2,
            "name": name,
            "status": "completed",
            "conclusion": "success",
            "steps": [
                {"name": step, "status": "completed", "conclusion": "success"}
                for step in steps
            ],
        }
        for index, (name, steps) in enumerate(attest.MANDATORY_STEPS.items(), start=1)
    ]


class TestMandatoryPredicates:
    def test_complete_same_attempt_qualifies(self, full_jobs: list[dict]) -> None:
        assert len(attest.qualified_jobs(full_jobs, run_id=1, attempt=2)) == 12

    @pytest.mark.parametrize(
        "conclusion", ["skipped", "neutral", "failure", "cancelled", "timed_out"]
    )
    def test_source_scan_requires_exact_success(
        self, full_jobs: list[dict], conclusion: str
    ) -> None:
        scan = next(
            s for s in full_jobs[0]["steps"] if s["name"] == "Trivy filesystem scan"
        )
        scan["conclusion"] = conclusion
        assert attest.qualified_jobs(full_jobs, run_id=1, attempt=2) is None

    def test_missing_source_scan_refuses(self, full_jobs: list[dict]) -> None:
        full_jobs[0]["steps"] = [
            s for s in full_jobs[0]["steps"] if s["name"] != "Trivy filesystem scan"
        ]
        assert attest.qualified_jobs(full_jobs, run_id=1, attempt=2) is None

    def test_carried_over_job_refuses(self, full_jobs: list[dict]) -> None:
        full_jobs[0]["run_attempt"] = 1
        assert attest.qualified_jobs(full_jobs, run_id=1, attempt=2) is None

    def test_unknown_attempt_semantics_refuse(self, full_jobs: list[dict]) -> None:
        del full_jobs[0]["run_attempt"]
        with pytest.raises(attest.AttestationError, match="unknown_job_attempt"):
            attest.qualified_jobs(full_jobs, run_id=1, attempt=2)

    @pytest.mark.parametrize("index", range(12))
    def test_every_mandatory_leg_is_required(
        self, full_jobs: list[dict], index: int
    ) -> None:
        del full_jobs[index]
        assert attest.qualified_jobs(full_jobs, run_id=1, attempt=2) is None

    def test_failed_optional_retry_does_not_replace_final_gates(
        self, full_jobs: list[dict]
    ) -> None:
        job = next(j for j in full_jobs if j["name"] == "build-image-and-scan")
        job["steps"].append(
            {
                "name": "Trivy image scan (report, attempt 1)",
                "status": "completed",
                "conclusion": "failure",
            }
        )
        assert attest.qualified_jobs(full_jobs, run_id=1, attempt=2) is not None


class TestWorkflowInventory:
    def test_reviewed_workflow_bytes_bind_command_and_action_semantics(self) -> None:
        attest.require_workflow(ROOT)

    def test_every_named_predicate_is_present_in_its_workflow_leg(self) -> None:
        jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
        for name, predicates in attest.MANDATORY_STEPS.items():
            job_id = (
                "unit"
                if name.startswith("unit (")
                else "integration-shard"
                if name.startswith("integration (")
                else name
            )
            names = [s.get("name") for s in jobs[job_id]["steps"]]
            assert all(names.count(predicate) == 1 for predicate in predicates)

    def test_changed_workflow_cannot_attest_with_old_inventory(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / ".github/workflows/ci.yml"
        path.parent.mkdir(parents=True)
        original = (ROOT / ".github/workflows/ci.yml").read_text()
        changed = original.replace('exit-code: "1"', 'exit-code: "0"', 1)
        assert changed != original
        path.write_text(changed)
        with pytest.raises(attest.AttestationError, match="workflow_inventory_drift"):
            attest.require_workflow(tmp_path)


class TestEveryPredicate:
    @pytest.mark.parametrize(
        "job_name,step_name",
        [
            (job, step)
            for job, steps in attest.MANDATORY_STEPS.items()
            for step in steps
        ],
    )
    @pytest.mark.parametrize("mutation", ["missing", "skipped"])
    def test_each_enforcing_predicate_is_required(
        self, full_jobs: list[dict], job_name: str, step_name: str, mutation: str
    ) -> None:
        job = next(job for job in full_jobs if job["name"] == job_name)
        step = next(step for step in job["steps"] if step["name"] == step_name)
        if mutation == "missing":
            job["steps"].remove(step)
        else:
            step["conclusion"] = "skipped"
        assert attest.qualified_jobs(full_jobs, run_id=1, attempt=2) is None


@pytest.fixture
def producer_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, full_jobs: list[dict]
):
    import json
    import subprocess

    root = tmp_path / "repository"
    root.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            [
                "git",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                *args,
            ],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()

    git("init", "--quiet")
    git("commit", "--quiet", "--allow-empty", "-m", "parent")
    workflow = root / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_bytes((ROOT / ".github/workflows/ci.yml").read_bytes())
    git("add", ".github/workflows/ci.yml")
    git("commit", "--quiet", "-m", "source")
    head = git("rev-parse", "HEAD")
    environment = {
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_RUN_ID": "1",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_SHA": head,
        "GITHUB_REPOSITORY": "hydrosolutions/SAPPHIRE_flow",
        "CI_NEEDS": json.dumps(
            {
                name: {"result": "success"}
                for name in (
                    "lint",
                    "unit",
                    "unit-coverage",
                    "wheel-only-guard",
                    "integration-shard",
                    "integration",
                    "build-image-and-scan",
                    "aquacast-release-image-proof",
                )
            }
        ),
    }
    for job in full_jobs:
        job.update(
            started_at="2026-11-01T00:01:00Z", completed_at="2026-11-01T00:02:00Z"
        )
    calls = []

    class Reader:
        def __init__(self, token: str, budget: object) -> None:
            assert token == "synthetic-token"

        def metadata(self, route: str) -> dict:
            calls.append(route)
            return {
                "id": 1,
                "run_attempt": 2,
                "head_sha": head,
                "run_started_at": "2026-11-01T00:00:00Z",
            }

        def items(self, route: str, key: str) -> list[dict]:
            assert key == "jobs"
            calls.append(route)
            return full_jobs

        def forget_credential(self) -> None:
            calls.append("credential_retired")

    tool = attest.provenance_tool()
    monkeypatch.setattr(tool, "GitHubReader", Reader)
    monkeypatch.setattr(attest, "__file__", str(root / "tools/ci_attest.py"))
    return root, environment, full_jobs, calls


class TestProducerExecution:
    def test_cli_emits_full_evidence_for_exact_checkout(
        self, producer_case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import json
        import os

        root, environment, jobs, calls = producer_case
        output = tmp_path / "artifact/ci-proof.json"
        outputs = tmp_path / "outputs"
        for key, value in environment.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("GH_TOKEN", "synthetic-token")
        monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
        monkeypatch.setattr(sys, "argv", ["ci_attest", "--output", str(output)])
        assert attest.main() == 0
        document = json.loads(output.read_bytes())
        assert document["qualification"] == "FULL"
        assert document["checkout"] == attest.commit_facts(
            root, environment["GITHUB_SHA"], 10
        )
        assert len(document["mandatory_jobs"]) == 12
        assert outputs.read_text() == "qualified=true\n"
        assert "GH_TOKEN" not in os.environ
        assert calls == [
            "runs/1/attempts/2",
            "runs/1/attempts/2/jobs",
            "credential_retired",
        ]

    def test_missing_predicate_emits_no_artifact(
        self, producer_case, tmp_path: Path
    ) -> None:
        root, environment, jobs, calls = producer_case
        jobs[0]["steps"].pop()
        output = tmp_path / "artifact/ci-proof.json"
        assert not attest.produce(root, output, environment, "synthetic-token")
        assert not output.exists()
        assert calls[-1] == "credential_retired"

    def test_failed_needs_does_not_query_api(
        self, producer_case, tmp_path: Path
    ) -> None:
        import json

        root, environment, jobs, calls = producer_case
        needs = json.loads(environment["CI_NEEDS"])
        needs["aquacast-release-image-proof"]["result"] = "skipped"
        environment["CI_NEEDS"] = json.dumps(needs)
        output = tmp_path / "artifact/ci-proof.json"
        assert not attest.produce(root, output, environment, "synthetic-token")
        assert not output.exists()
        assert calls == []


class TestMainOnlyProducer:
    @pytest.mark.parametrize(
        "event,ref",
        [
            ("pull_request", "refs/pull/7/merge"),
            ("push", "refs/heads/feature"),
            (None, "refs/heads/main"),
            ("push", None),
        ],
    )
    def test_ineligible_context_does_not_touch_git_api_or_artifact(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        event: str | None,
        ref: str | None,
    ) -> None:
        environment = {}
        if event is not None:
            environment["GITHUB_EVENT_NAME"] = event
        if ref is not None:
            environment["GITHUB_REF"] = ref
        monkeypatch.setattr(
            attest,
            "require_workflow",
            lambda *args: pytest.fail(
                "ineligible event cannot inspect workflow or git"
            ),
        )
        monkeypatch.setattr(
            attest,
            "provenance_tool",
            lambda: pytest.fail("ineligible event cannot query API"),
        )
        output = tmp_path / "artifact/ci-proof.json"
        assert not attest.produce(tmp_path, output, environment, "synthetic-token")
        assert not output.parent.exists()

    def test_terminal_workflow_is_push_main_only(self) -> None:
        jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
        assert (
            jobs["ci-attest"]["if"]
            == "${{ always() && !cancelled() && github.event_name == 'push' "
            "&& github.ref == 'refs/heads/main' }}"
        )
