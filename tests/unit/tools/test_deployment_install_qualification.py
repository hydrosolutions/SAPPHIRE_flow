from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest

from sapphire_flow.config.deployment_profile import select_deployment_profile
from tests.integration import test_deployment_install as qualification
from tests.unit.tools.test_deployment_install_runtime import install
from tests.unit.tools.test_deployment_install_runtime import setup as setup
from tools import deployment_install_runtime as runtime
from tools import deployment_install_state as state

if TYPE_CHECKING:
    from pathlib import Path


class TestQualificationBoundaries:
    def test_materialized_profile_passes_real_offline_preview(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        revision = "a" * 40
        profile, installation, canaries = qualification.prepare_case(
            tmp_path, revision, "ci-proof-" + revision
        )

        def identity(*args: Any) -> str:
            return revision

        monkeypatch.setattr(runtime.common, "source_identity", identity)
        selection = select_deployment_profile(
            profile_file=profile,
            installation_file=installation,
            ambient_environment={},
        )
        prepared = runtime.preview.prepare(
            selection,
            runtime.preview.InstallationLayout(state_dir=tmp_path / "state"),
            identity_kind="ci-development",
        )
        assert prepared.preview.status == "PLAN_READY"
        assert selection.installation is not None
        assert selection.installation.project_name.startswith("sapphire-install-test-")
        assert len(set(canaries)) == 5
        assert not (tmp_path / "state").exists()

    def test_teardown_refuses_non_qualification_project(
        self, setup: tuple[Any, ...]
    ) -> None:
        _, layout, fake, _ = setup
        assert install(setup)[0] == 0
        before = len(fake.events)
        with pytest.raises(AssertionError, match="not a qualification installation"):
            qualification.teardown_case(layout.state_dir)
        assert fake.events[before:] == []

    def test_teardown_rechecks_foreign_labels_before_deletion(
        self, setup: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, layout, fake, _ = setup
        assert install(setup)[0] == 0
        record = state.load_record(layout.state_dir)
        api = fake.find("container", record.project + "-api-1")
        api["Config"]["Labels"][runtime.LABEL] = "foreign"

        def owned(directory: Path, deadline: float) -> runtime.Session:
            return runtime.Session(
                runtime.common.Runtime(
                    directory=directory, deadline=deadline, executor=fake
                ),
                record,
            )

        monkeypatch.setattr(qualification, "session", owned)
        before = len(fake.events)
        with pytest.raises(state.InstallError, match="foreign_resource_refused"):
            qualification.teardown_case(layout.state_dir)
        assert fake.events[before:] == []

    def test_moved_state_is_not_teardown_authority(
        self, setup: tuple[Any, ...]
    ) -> None:
        _, layout, _, _ = setup
        assert install(setup)[0] == 0
        record = state.load_record(layout.state_dir)
        state.save_record(
            layout.state_dir, replace(record, state_directory="/another/place")
        )
        with pytest.raises(state.InstallError, match="installation_state_path_changed"):
            qualification.teardown_case(layout.state_dir)

    def test_missing_record_does_not_claim_cleanup(self, tmp_path: Path) -> None:
        assert qualification.teardown_case(tmp_path) == "not_created"

    def test_exhausted_budget_does_not_launch_child(self, tmp_path: Path) -> None:
        with pytest.raises(AssertionError, match="budget exhausted before launch"):
            qualification.invoke(
                "install",
                tmp_path / "state",
                profile=tmp_path / "profile.toml",
                installation=tmp_path / "installation.toml",
                canaries=(),
                deadline=0,
                outcomes=[],
            )

    @pytest.mark.parametrize("location", ["state", "environment", "evidence"])
    def test_canary_in_any_retained_output_refuses(
        self, setup: tuple[Any, ...], location: str
    ) -> None:
        _, layout, fake, _ = setup
        assert install(setup)[0] == 0
        record = state.load_record(layout.state_dir)
        owned = runtime.Session(
            runtime.common.Runtime(
                directory=layout.state_dir,
                deadline=fake.clock() + 120,
                clock=fake.clock,
                executor=fake,
            ),
            record,
        )
        canary = "PRIVATE_QUALIFICATION_CANARY"
        evidence: dict[str, Any] = {}
        if location == "state":
            (layout.state_dir / "accidental-output").write_text(canary)
        elif location == "environment":
            fake.find("container", record.project + "-api-1")["Config"]["Env"].append(
                "DB_PASSWORD=" + canary
            )
        else:
            evidence["code"] = canary
        with pytest.raises(AssertionError, match="credential escaped"):
            qualification.assert_private_outputs(owned, (canary,), evidence)


class TestQualificationEvidence:
    def test_failure_summary_uses_private_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        report = tmp_path / "evidence" / "qualification.json"
        monkeypatch.setenv("SAPPHIRE_INSTALL_ACCEPTANCE", "dedicated-test-daemon")
        monkeypatch.setenv("SAPPHIRE_INSTALL_IMAGE_TAG", "ci-proof-test")
        monkeypatch.setenv("SAPPHIRE_INSTALL_QUALIFICATION_REPORT", str(report))

        def fail(*args: Any) -> None:
            raise AssertionError("injected qualification failure")

        monkeypatch.setattr(qualification, "success_case", fail)
        with pytest.raises(AssertionError, match="injected qualification failure"):
            qualification.TestPersistentInstallation().test_lifecycle_failure_and_owned_cleanup(
                tmp_path
            )
        result = json.loads(report.read_text())
        assert result["passed"] is False
        assert result["installation_cleanup_verified"] is False
        assert report.parent.stat().st_mode & 0o777 == 0o700
        assert report.stat().st_mode & 0o777 == 0o600

    def test_public_evidence_directory_refuses_before_installation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        evidence = tmp_path / "evidence"
        evidence.mkdir(mode=0o755)
        evidence.chmod(0o755)
        monkeypatch.setenv("SAPPHIRE_INSTALL_ACCEPTANCE", "dedicated-test-daemon")
        monkeypatch.setenv("SAPPHIRE_INSTALL_IMAGE_TAG", "ci-proof-test")
        monkeypatch.setenv(
            "SAPPHIRE_INSTALL_QUALIFICATION_REPORT",
            str(evidence / "qualification.json"),
        )

        def unexpected(*args: Any) -> None:
            pytest.fail("installation started before evidence preflight")

        monkeypatch.setattr(qualification, "success_case", unexpected)
        with pytest.raises(
            runtime.common.RehearsalRuntimeError, match="private_directory_required"
        ):
            qualification.TestPersistentInstallation().test_lifecycle_failure_and_owned_cleanup(
                tmp_path
            )
