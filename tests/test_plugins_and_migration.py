from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import code_review.cli as cli_module
from code_review.review_planner.init import load_state
from code_review.review_planner.migration import CURRENT_SCHEMA_VERSION, migrate_config_payload, migrate_state_payload
from code_review.review_planner.plugins.governance import StrictHumanApprovalGovernance
from code_review.review_planner.plugins.sandbox import build_default_sandbox_registry
from code_review.review_planner.render import to_catalog_markdown


def test_migrate_config_payload_renames_deprecated_keys() -> None:
    payload = {"reviewer_personas": ["correctness"], "methodology_packs": ["methodology-core"], "schema_version": 1}
    migrated, notes = migrate_config_payload(payload)
    assert migrated["personas"] == ["correctness"]
    assert migrated["baselines"] == ["methodology-core"]
    assert migrated["schema_version"] == CURRENT_SCHEMA_VERSION
    assert notes


def test_migrate_config_payload_rejects_conflicting_keys() -> None:
    payload = {"reviewer_personas": ["correctness"], "personas": ["security"]}
    try:
        migrate_config_payload(payload)
        raise AssertionError("Expected conflict error")
    except ValueError as error:
        assert "Conflicting config keys" in str(error)


def test_migrate_state_payload_renames_selection_keys() -> None:
    payload = {"selections": {"reviewer_personas": ["correctness"]}, "schema_version": 1}
    migrated, notes = migrate_state_payload(payload)
    assert migrated["selections"]["personas"] == ["correctness"]
    assert migrated["schema_version"] == CURRENT_SCHEMA_VERSION
    assert notes


def test_migrate_state_payload_rejects_conflicting_selection_keys() -> None:
    payload = {"selections": {"reviewer_personas": ["correctness"], "personas": ["security"]}}
    try:
        migrate_state_payload(payload)
        raise AssertionError("Expected conflict error")
    except ValueError as error:
        assert "Conflicting state selection keys" in str(error)


def test_load_state_applies_migration(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({"selections": {"reviewer_personas": ["correctness"]}, "schema_version": 1}), encoding="utf-8"
    )
    loaded = load_state(state_path)
    assert loaded["selections"]["personas"] == ["correctness"]
    assert loaded["schema_version"] == CURRENT_SCHEMA_VERSION


def test_governance_strict_blocks_noninteractive_publish() -> None:
    plugin = StrictHumanApprovalGovernance()
    decision = plugin.decide_pr_publish(
        requested_action="comment",
        active_pr={"number": 1},
        interactive=False,
    )
    assert decision.approved is False
    assert "explicit human approval required" in decision.reason


def test_finalize_review_output_blocks_publish_when_governance_denies(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(cli_module, "write_feedback_report", lambda target, plan: tmp_path / "latest.json")
    monkeypatch.setattr(cli_module, "update_feedback_state", lambda target, plan: tmp_path / "state.json")
    monkeypatch.setattr(cli_module, "write_feedback_learning_queue", lambda target: tmp_path / "learning-queue.json")
    monkeypatch.setattr(
        cli_module,
        "_detect_active_pull_request",
        lambda review_target, pr_ref=None, provider_preference="auto": {"number": 42, "provider": "github"},
    )
    monkeypatch.setattr(cli_module, "_resolve_post_review_action", lambda requested, active_pr: "comment")
    monkeypatch.setattr(cli_module, "_publish_pr_comment", lambda review_target, plan: "SHOULD NOT RUN")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    args = SimpleNamespace(
        emit="markdown",
        feedback_status=[],
        learn_accepted_feedback=False,
        learn_feedback_id=[],
        post_review_action="comment",
        governance_plugin="strict-human-approval",
    )
    cli_module._finalize_review_output(
        plan={"feedback_actions": [], "tool_review_results": [], "units": []}, review_target=tmp_path, args=args
    )
    output = capsys.readouterr().out
    assert "Publish blocked: explicit human approval required in interactive mode." in output


def test_run_review_effects_sets_execution_fallback(monkeypatch) -> None:
    attempts = {"count": 0}

    class FailingPlugin:
        id = "shell-local"

        def run_setup(self, *, deterministic_gates, interactive=None):
            attempts["count"] += 1
            raise RuntimeError("primary failed")

        def run_review(self, *, target, deterministic_gates, interactive=None):
            return ([{"id": "python-ruff", "status": "passed", "steps": []}], None)

    class FallbackPlugin:
        id = "fallback-plugin"

        def run_setup(self, *, deterministic_gates, interactive=None):
            return ([{"id": "python-ruff", "status": "passed", "steps": []}], None)

        def run_review(self, *, target, deterministic_gates, interactive=None):
            return ([{"id": "python-ruff", "status": "passed", "steps": []}], None)

    registry = SimpleNamespace(
        plugins={"shell-local": FailingPlugin(), "fallback-plugin": FallbackPlugin()},
        resolve=lambda plugin_id: {"shell-local": FailingPlugin(), "fallback-plugin": FallbackPlugin()}[
            plugin_id or "shell-local"
        ],
    )
    monkeypatch.setattr(cli_module, "build_default_execution_registry", lambda **kwargs: registry)

    plan = {"deterministic_gates": [{"id": "python-ruff"}], "feedback_actions": [], "feedback": [], "units": []}
    updated = cli_module._run_review_effects(
        plan=plan,
        review_target=Path("."),
        execution_plugin_id="shell-local",
        execution_fallback_plugin_id="fallback-plugin",
    )
    assert updated["execution_plugin"] == "shell-local"
    assert updated["execution_plugin_fallback"] == "fallback-plugin"
    assert attempts["count"] == 1


def test_catalog_markdown_lists_plugin_capabilities() -> None:
    catalog = to_catalog_markdown({}, {}, {}, {}, {}, {})
    assert "Provider plugins" in catalog
    assert "Execution plugins" in catalog
    assert "Sandbox plugins" in catalog
    assert "Governance plugins" in catalog


def test_sandbox_registry_defaults_to_scratch_home() -> None:
    registry = build_default_sandbox_registry()
    assert sorted(registry.plugins) == ["passthrough", "scratch-home"]
    assert registry.resolve(None).id == "scratch-home"


def test_scratch_home_sandbox_exposes_isolated_home_and_xdg(tmp_path: Path) -> None:
    session = build_default_sandbox_registry().resolve("scratch-home").enter(target=tmp_path)
    try:
        assert session.id == "scratch-home"
        assert session.environment["HOME"] != str(tmp_path)
        assert session.environment["XDG_CONFIG_HOME"].startswith(session.environment["HOME"])
        assert session.environment["XDG_CACHE_HOME"].startswith(session.environment["HOME"])
        assert session.environment["XDG_STATE_HOME"].startswith(session.environment["HOME"])
    finally:
        session.cleanup()
