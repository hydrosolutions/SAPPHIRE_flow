from __future__ import annotations

from sapphire_flow.flows.compute_skills import compute_skills_task


class TestMultiParameterImport:
    def test_compute_skills_task_is_importable(self) -> None:
        assert hasattr(compute_skills_task, "map")

    def test_compute_skills_task_has_fn(self) -> None:
        assert callable(compute_skills_task.fn)
