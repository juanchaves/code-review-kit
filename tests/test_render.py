from __future__ import annotations

import pytest

from code_review.review_planner.render import render_tool_setup_results


def test_render_tool_setup_results_is_owned_by_render_module() -> None:
    assert render_tool_setup_results.__module__ == "code_review.review_planner.render"


def test_render_tool_setup_results_empty() -> None:
    output = render_tool_setup_results([])

    assert "No selected tool packs required setup." in output
    assert "## Tool setup execution" in output


def test_render_tool_setup_results_with_steps() -> None:
    results = [
        {
            "id": "shell-shellcheck",
            "status": "passed",
            "steps": [
                {"kind": "setup", "text": "sudo apt-get install -y shellcheck", "status": "passed"},
                {"kind": "verify", "text": "shellcheck **/*.sh", "status": "passed"},
            ],
        }
    ]

    output = render_tool_setup_results(results)

    assert "Platform:" in output
    assert "- **shell-shellcheck**: passed" in output
    assert "  - setup: `sudo apt-get install -y shellcheck` (passed)" in output
    assert "  - verify: `shellcheck **/*.sh` (passed)" in output


def test_render_tool_setup_results_not_importable_from_init() -> None:
    with pytest.raises(ImportError):
        from code_review.review_planner.init import render_tool_setup_results  # noqa: F401
