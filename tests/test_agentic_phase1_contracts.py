from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import code_review.cli as cli_module
from code_review.review_planner.init import (
    apply_feedback_status_updates,
    default_feedback_learning_queue_path,
    default_feedback_state_path,
    promote_accepted_feedback_to_learnings,
    update_feedback_state,
    write_feedback_learning_queue,
)


def test_bdd_feedback_queue_contract(tmp_path: Path) -> None:
    # Given a review run with actionable feedback items
    plan = {
        "feedback_actions": [
            {"id": "missing-language-pack", "priority": "P2", "title": "Missing language", "action": "Select language pack", "why": "coverage"},
        ]
    }
    update_feedback_state(target=tmp_path, plan=plan)

    # When the learning queue artifact is generated
    queue_path = write_feedback_learning_queue(target=tmp_path)

    # Then the queue contains active actionable feedback in deterministic order
    payload = json.loads(queue_path.read_text(encoding="utf-8"))
    assert queue_path == default_feedback_learning_queue_path(target=tmp_path)
    assert payload["items"][0]["id"] == "missing-language-pack"
    assert payload["items"][0]["status"] == "open"


def test_bdd_approval_gated_learning_promotion_contract(tmp_path: Path) -> None:
    # Given feedback exists and only some items are explicitly accepted by a human
    plan = {
        "feedback_actions": [
            {"id": "accepted-item", "priority": "P1", "title": "Accepted", "action": "Use provider-aware PR fallback", "why": "reliability"},
            {"id": "not-accepted-item", "priority": "P2", "title": "Not accepted", "action": "Keep investigating", "why": "unclear"},
        ]
    }
    update_feedback_state(target=tmp_path, plan=plan)
    apply_feedback_status_updates(target=tmp_path, updates=["accepted-item:accepted"])

    # When promotion runs
    result = promote_accepted_feedback_to_learnings(target=tmp_path)

    # Then only accepted items are promoted and marked done
    assert result["promoted_ids"] == ["accepted-item"]
    state = json.loads(default_feedback_state_path(target=tmp_path).read_text(encoding="utf-8"))
    assert state["items"]["accepted-item"]["status"] == "done"
    assert state["items"]["not-accepted-item"]["status"] == "open"


def test_atdd_review_finalize_writes_queue_contract(monkeypatch, tmp_path: Path, capsys) -> None:
    # Given review finalization is executed in markdown mode
    monkeypatch.setattr(cli_module, "write_feedback_report", lambda target, plan: tmp_path / "latest.json")
    monkeypatch.setattr(cli_module, "update_feedback_state", lambda target, plan: tmp_path / "state.json")
    monkeypatch.setattr(cli_module, "write_feedback_learning_queue", lambda target: tmp_path / "learning-queue.json")
    monkeypatch.setattr(
        cli_module,
        "_detect_active_pull_request",
        lambda review_target, pr_ref=None, provider_preference="auto": None,
    )
    monkeypatch.setattr(cli_module, "_resolve_post_review_action", lambda requested, active_pr: "skip")

    # When finalization runs
    args = SimpleNamespace(
        emit="markdown",
        feedback_status=[],
        learn_accepted_feedback=False,
        learn_feedback_id=[],
        post_review_action="skip",
    )
    cli_module._finalize_review_output(
        plan={"feedback_actions": [], "tool_review_results": [], "units": []},
        review_target=tmp_path,
        args=args,
    )

    # Then the completion output includes the learning queue artifact path
    output = capsys.readouterr().out
    assert "Learning queue:" in output
