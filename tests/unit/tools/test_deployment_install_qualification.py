from __future__ import annotations

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
