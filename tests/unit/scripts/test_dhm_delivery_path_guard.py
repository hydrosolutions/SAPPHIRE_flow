from scripts.guard_dhm_delivery_paths import rejected_paths


def test_only_two_exact_documentation_examples_are_allowed() -> None:
    assert (
        rejected_paths(
            [
                "docs/requirements/DFL_Dummy Station A.txt",
                "docs/requirements/RT_Dummy Station A.txt",
                "notes.txt",
            ]
        )
        == []
    )


def test_restricted_names_are_rejected_at_root_and_nested() -> None:
    assert rejected_paths(
        [
            "DFL_synthetic.txt",
            "nested/RT_synthetic.txt",
            "other/DFL_Dummy Station A.txt",
        ]
    ) == [
        "DFL_synthetic.txt",
        "nested/RT_synthetic.txt",
        "other/DFL_Dummy Station A.txt",
    ]
