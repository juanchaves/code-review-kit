from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import code_review.cli as cli_module
import code_review.review_planner.init
from code_review.review_planner.catalog import TOOL_PACKS
from code_review.review_planner.init import (
    apply_bootstrap,
    apply_feedback_status_updates,
    build_bootstrap_artifacts,
    choose_pull_request_reference,
    choose_review_workflow_after_init,
    compute_deselections,
    default_feedback_learning_queue_path,
    default_feedback_report_path,
    default_feedback_state_path,
    promote_accepted_feedback_to_learnings,
    render_bootstrap_result,
    render_deselection_summary,
    render_init_plan,
    render_plan_summary,
    resolve_setup_tool_policy,
    run_bootstrap_with_status,
    run_deterministic_gates,
    run_selected_tool_setup,
    run_uninstall_commands,
    should_start_review_after_init,
    tool_review_feedback,
    tool_setup_feedback,
    uninstall_commands_for_tools,
    update_feedback_state,
    write_feedback_learning_queue,
    write_feedback_report,
)
from code_review.review_planner.learning import merge_learned_extensions, record_learned_practices
from code_review.review_planner.learning import default_learned_practices_path
from code_review.review_planner.plugins.providers import AdoPrProvider


def test_build_bootstrap_artifacts_includes_agent_and_instructions(tmp_path: Path) -> None:
    artifacts = build_bootstrap_artifacts(target=tmp_path, name="code-review")

    paths = {artifact.path.relative_to(tmp_path) for artifact in artifacts}
    assert paths == {
        Path(".github/agents/code-review.agent.md"),
        Path(".github/instructions/code-review.instructions.md"),
        Path(".github/copilot-instructions.md"),
        Path(".github/prompts/code-review.prompt.md"),
    }


def test_apply_bootstrap_writes_repo_local_files(tmp_path: Path) -> None:
    result = apply_bootstrap(target=tmp_path, name="code-review")

    assert sorted(result.created) == [
        Path(".github/agents/code-review.agent.md"),
        Path(".github/copilot-instructions.md"),
        Path(".github/instructions/code-review.instructions.md"),
        Path(".github/prompts/code-review.prompt.md"),
    ]
    assert (tmp_path / ".github/agents/code-review.agent.md").exists()
    assert (tmp_path / ".github/instructions/code-review.instructions.md").exists()
    assert (tmp_path / ".github/copilot-instructions.md").exists()
    assert (tmp_path / ".github/prompts/code-review.prompt.md").exists()


def test_apply_bootstrap_refreshes_stale_files(tmp_path: Path) -> None:
    (tmp_path / ".github/agents").mkdir(parents=True)
    (tmp_path / ".github/agents/code-review.agent.md").write_text("stale", encoding="utf-8")

    result = apply_bootstrap(target=tmp_path, name="code-review")

    assert Path(".github/agents/code-review.agent.md") in result.updated
    assert (tmp_path / ".github/agents/code-review.agent.md").read_text(encoding="utf-8").startswith("---")


def test_render_bootstrap_result_mentions_next_step(tmp_path: Path) -> None:
    result = apply_bootstrap(target=tmp_path, name="code-review")
    rendered = render_bootstrap_result(result, harness="copilot", name="code-review", wizard_started=False)

    assert "crk bootstrap" in rendered
    assert "Open your selected harness in this repository" in rendered
    assert "Harness: copilot" in rendered
    assert "Files" in rendered


def test_bootstrap_agent_starts_with_greeting(tmp_path: Path) -> None:
    artifacts = build_bootstrap_artifacts(target=tmp_path, name="code-review")
    agent = next(artifact for artifact in artifacts if artifact.path.name == "code-review.agent.md")
    prompt = next(artifact for artifact in artifacts if artifact.path.name == "code-review.prompt.md")

    assert "Ready — starting code-review workflow for the current repository now." in agent.content
    assert "Start immediately: run the code-review workflow on the current repository" in prompt.content
    assert "Post actionable comments to the active PR" in agent.content
    assert "Harness startup parity" in agent.content


def test_publish_pr_comment_uses_active_pull_request(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class FakeGithubProvider:
        id = "github"

        def detect_pull_request(self, *, review_target: Path, pr_ref: str | None = None):
            calls.append(("detect", pr_ref or ""))
            return {"number": 12, "title": "Fix docs", "provider": "github"}

        def publish_comment(self, *, review_target: Path, pull_request: dict, body: str) -> str:
            calls.append(("publish", str(pull_request["number"])))
            return f"Posted actionable PR comment to #{pull_request['number']}."

    fake_registry = SimpleNamespace(
        pr_providers={"github": FakeGithubProvider()},
        resolve_pr_provider=lambda **_kwargs: FakeGithubProvider(),
    )
    monkeypatch.setattr(cli_module, "build_default_provider_registry", lambda: fake_registry)

    plan = {
        "target": str(tmp_path),
        "review_scope": {"mode": "repo-current"},
        "review_axes": ["standards", "spec"],
        "feedback_actions": [
            {"priority": "P1", "title": "Fix docs", "action": "Update the prompt contract", "why": "Docs are stale"},
        ],
        "feedback_report": {"path": "/tmp/report.json"},
    }

    message = cli_module._publish_pr_comment(review_target=tmp_path, plan=plan)

    assert message == "Posted actionable PR comment to #12."
    assert ("detect", "") in calls
    assert ("publish", "12") in calls


def test_publish_pr_comment_uses_explicit_pull_request_reference(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    class FakeGithubProvider:
        id = "github"

        def detect_pull_request(self, *, review_target: Path, pr_ref: str | None = None):
            calls.append(pr_ref or "")
            return {"number": 42, "title": "Fix docs", "provider": "github"}

        def publish_comment(self, *, review_target: Path, pull_request: dict, body: str) -> str:
            return f"Posted actionable PR comment to #{pull_request['number']}."

    fake_registry = SimpleNamespace(
        pr_providers={"github": FakeGithubProvider()},
        resolve_pr_provider=lambda **_kwargs: FakeGithubProvider(),
    )
    monkeypatch.setattr(cli_module, "build_default_provider_registry", lambda: fake_registry)

    plan = {
        "target": str(tmp_path),
        "review_scope": {"mode": "repo-current"},
        "review_axes": ["standards", "spec"],
        "review_pr_ref": "42",
        "feedback_actions": [
            {"priority": "P1", "title": "Fix docs", "action": "Update prompt", "why": "Docs are stale"}
        ],
        "feedback_report": {"path": "/tmp/report.json"},
    }

    message = cli_module._publish_pr_comment(review_target=tmp_path, plan=plan)

    assert message == "Posted actionable PR comment to #42."
    assert calls == ["42"]


def test_detect_active_pull_request_uses_ado_for_azure_url(tmp_path: Path, monkeypatch) -> None:
    class FakeAdoProvider:
        id = "ado"

        def detect_pull_request(self, *, review_target: Path, pr_ref: str | None = None):
            return {"number": 7017, "provider": "ado", "title": "Improve workflow"}

        def publish_comment(self, *, review_target: Path, pull_request: dict, body: str) -> str:
            return "ok"

    fake_registry = SimpleNamespace(
        pr_providers={"ado": FakeAdoProvider(), "github": FakeAdoProvider()},
        resolve_pr_provider=lambda **_kwargs: FakeAdoProvider(),
    )
    monkeypatch.setattr(cli_module, "build_default_provider_registry", lambda: fake_registry)

    pr = cli_module._detect_active_pull_request(
        review_target=tmp_path,
        pr_ref="https://dev.azure.com/sample-org/SampleProject/_git/sample-repo/pullrequest/7017",
    )

    assert pr is not None
    assert pr["provider"] == "ado"
    assert pr["number"] == 7017


def test_publish_pr_comment_posts_to_ado_pull_request(tmp_path: Path, monkeypatch) -> None:
    class FakeAdoProvider:
        id = "ado"

        def detect_pull_request(self, *, review_target: Path, pr_ref: str | None = None):
            return {
                "number": 7017,
                "title": "Improve workflow",
                "provider": "ado",
                "project": "SampleProject",
                "repository_id": "repo-id-1",
            }

        def publish_comment(self, *, review_target: Path, pull_request: dict, body: str) -> str:
            return f"Posted actionable PR comment to ADO PR #{pull_request['number']}."

    fake_registry = SimpleNamespace(
        pr_providers={"ado": FakeAdoProvider(), "github": FakeAdoProvider()},
        resolve_pr_provider=lambda **_kwargs: FakeAdoProvider(),
    )
    monkeypatch.setattr(cli_module, "build_default_provider_registry", lambda: fake_registry)

    plan = {
        "target": str(tmp_path),
        "review_scope": {"mode": "repo-current"},
        "review_axes": ["standards", "spec"],
        "review_pr_ref": "7017",
        "feedback_actions": [
            {"priority": "P1", "title": "Fix docs", "action": "Update prompt", "why": "Docs are stale"}
        ],
        "feedback_report": {"path": "/tmp/report.json"},
    }

    message = cli_module._publish_pr_comment(review_target=tmp_path, plan=plan)

    assert message == "Posted actionable PR comment to ADO PR #7017."


def test_extract_line_comment_threads_normalizes_paths_and_limits_count() -> None:
    plan = {
        "findings": [
            {
                "file": "src/main.py",
                "line": 12,
                "issue": "Missing guard",
                "recommendation": "Add null check",
                "severity": "blocking",
            },
            {
                "file": "/src/app.py",
                "line": 3,
                "issue": "Prefer explicit type",
                "recommendation": "",
                "severity": "suggestion",
            },
        ]
    }
    threads = cli_module._extract_line_comment_threads(plan)
    assert threads[0]["file_path"] == "/src/main.py"
    assert threads[0]["line"] == 12
    assert "Next step: Add null check" in threads[0]["content"]
    assert threads[1]["file_path"] == "/src/app.py"


def test_render_pr_comment_body_uses_conventional_comments_and_avoids_local_paths() -> None:
    plan = {
        "target": "/Users/example/work/repo",
        "feedback_actions": [
            {
                "id": "deterministic-review-gate-failed",
                "priority": "P1",
                "title": "Review gate failed for python-ruff",
                "action": "Fix `uvx ruff check .` and rerun the selected review gate.",
                "why": "Tool review failed for python-ruff: uvx ruff check .",
            },
            {
                "id": "ui-ux-persona-not-selected",
                "priority": "P3",
                "title": "Consider enabling the UX reviewer persona",
                "action": "Add the `ux` persona to evaluate comment readability and action hierarchy in this run.",
                "why": "UI/UX specialties are selected without the dedicated UX reviewer persona.",
            },
        ],
        "feedback_report": {"path": "/Users/example/work/repo/.code-review/feedback/latest.json"},
        "requirements_compliance": {"issue_provider": "ado", "issue_ref": "7017"},
    }
    body = cli_module._render_pr_comment_body(
        plan=plan,
        pull_request={"number": 7017, "provider": "ado", "title": "Some title"},
    )

    assert "Conventional Comments" in body
    assert "**What failed:** Review gate failed for python-ruff." in body
    assert "**How urgent:** 1 blocking, 1 non-blocking." in body
    assert "**What to do next:**" in body
    assert "**issue (blocking):** Review gate failed for python-ruff" in body
    assert "**note (non-blocking):** Consider enabling the UX reviewer persona" in body
    assert "<summary>Context (optional)</summary>" in body
    assert "- Pull request ID: `7017`" in body
    assert "- Work item: `7017`" in body
    assert "Target:" not in body
    assert "Feedback report:" not in body
    assert "#7017" not in body


def test_render_pr_comment_body_splits_tooling_from_review_findings() -> None:
    plan = {
        "feedback_actions": [
            {
                "id": "deterministic-tool-gate-failed",
                "priority": "P1",
                "title": "Tool gate failed",
                "action": "Fix tool",
                "why": "failed",
            },
            {
                "id": "spec-axis-empty",
                "priority": "P2",
                "title": "Spec axis has no requirements context",
                "action": "Add requirements",
                "why": "missing",
            },
        ]
    }
    body = cli_module._render_pr_comment_body(
        plan=plan,
        pull_request={"number": 42, "provider": "github"},
    )

    assert "### Tooling status" in body
    assert "### Review findings" in body


def test_render_pr_comment_body_uses_pr_label_for_github_provider() -> None:
    plan = {"feedback_actions": []}
    body = cli_module._render_pr_comment_body(
        plan=plan,
        pull_request={"number": 42, "provider": "github"},
    )

    assert "- PR: `#42`" in body
    assert "Pull request ID" not in body


def test_render_init_plan_uses_table_format() -> None:
    rendered = render_init_plan(harness="copilot", name="code-review")

    assert "crk init" in rendered
    assert "Harness" in rendered


def test_render_plan_summary_uses_table_format() -> None:
    plan = {
        "target": "/repo",
        "selections": {
            "personas": ["correctness"],
            "baselines": ["methodology-core"],
            "tools": ["python-ruff"],
            "languages": ["python"],
            "specialties": [],
            "strategies": [],
        },
        "token_strategy": {
            "model_routing": "right-size",
            "cache_mode": "prompt",
        },
        "deterministic_gates": [
            {
                "id": "python-ruff",
                "category": "python",
                "why": "Fast Python linting and formatting.",
                "setup": ["uv installed"],
                "commands": ["uvx ruff check", "uvx ruff format --check"],
            }
        ],
        "units": [{}, {}],
        "feedback": ["Plan shape looks healthy."],
    }

    rendered = render_plan_summary(plan)

    assert "crk review summary" in rendered
    assert "Feedback" not in rendered
    assert "review summary" in rendered
    assert "Tooling setup" in rendered
    assert "prerequisite/setup" in rendered


def test_shell_tool_setup_mentions_linux_wsl_path() -> None:
    assert "Linux/WSL" in TOOL_PACKS["shell-shellcheck"].setup[0]


def test_run_selected_tool_setup_uses_linux_wsl_branch(monkeypatch) -> None:
    commands: list[str] = []
    call_count = 0

    def fake_run_shell(command: str) -> SimpleNamespace:
        nonlocal call_count
        commands.append(command)
        # First call is the pre-flight verify probe; simulate tool not yet installed
        call_count += 1
        returncode = 1 if call_count == 1 else 0
        return SimpleNamespace(returncode=returncode, stderr="")

    monkeypatch.setattr("code_review.review_planner.init.platform_label", lambda: "Linux/WSL")
    monkeypatch.setattr("code_review.review_planner.init.shutil.which", lambda name: "/usr/bin/uv")
    monkeypatch.setattr("code_review.review_planner.init._run_shell_command", fake_run_shell)

    results, error = run_selected_tool_setup(
        deterministic_gates=[
            {
                "id": "shell-shellcheck",
                "title": "ShellCheck",
                "setup": [
                    "macOS: brew install shellcheck; Linux/WSL: sudo apt-get update && sudo apt-get install -y shellcheck"
                ],
                "commands": ["shellcheck **/*.sh **/*.bash **/*.zsh"],
            }
        ]
    )

    assert error is None
    assert results[0]["status"] == "passed"
    assert commands == [
        "shellcheck **/*.sh **/*.bash **/*.zsh",   # pre-flight probe (fails → triggers setup)
        "sudo apt-get update && sudo apt-get install -y shellcheck",
        "shellcheck **/*.sh **/*.bash **/*.zsh",
    ]


def test_run_selected_tool_setup_blocks_unapproved_commands_in_non_interactive_mode(monkeypatch) -> None:
    commands: list[str] = []

    def fake_run_shell(command: str) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("code_review.review_planner.init.platform_label", lambda: "macOS")
    monkeypatch.setattr("code_review.review_planner.init._run_shell_command", fake_run_shell)

    results, error = run_selected_tool_setup(
        deterministic_gates=[
            {
                "id": "python-ruff",
                "title": "Ruff",
                "setup": [],
                "commands": ["uvx ruff --version"],
            }
        ],
        approval_policy={"mode": "prompt", "approved_commands": []},
        interactive=False,
    )

    assert "not approved" in str(error)
    assert results[0]["steps"][0]["status"] == "blocked"
    assert commands == []


def test_run_selected_tool_setup_succeeds_after_platform_label_rename() -> None:
    results, error = run_selected_tool_setup(deterministic_gates=[])

    assert error is None
    assert results == []


def test_platform_label_rename_is_total() -> None:
    assert not hasattr(code_review.review_planner.init, "_platform_label")


def test_should_start_review_after_init_respects_action(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    assert should_start_review_after_init(harness="copilot", action="ask") is True
    assert should_start_review_after_init(harness="copilot", action="start") is True
    assert should_start_review_after_init(harness="copilot", action="exit") is False
    assert should_start_review_after_init(harness="claude-code", action="ask") is True


def test_choose_review_workflow_after_init_respects_mode_and_prompt(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "p")
    assert choose_review_workflow_after_init(harness="copilot", workflow="ask") == "pr-review"
    assert choose_review_workflow_after_init(harness="copilot", workflow="dev-loop") == "dev-loop"
    assert choose_review_workflow_after_init(harness="copilot", workflow="pr-review") == "pr-review"
    assert choose_review_workflow_after_init(harness="claude-code", workflow="ask") == "pr-review"


def test_choose_pull_request_reference_prefers_provided_and_allows_prompt(monkeypatch) -> None:
    assert choose_pull_request_reference(provided="123") == "123"
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "https://github.com/org/repo/pull/55")
    assert choose_pull_request_reference() == "https://github.com/org/repo/pull/55"


def test_resolve_setup_tool_policy_uses_previous_and_allows_reset() -> None:
    previous_state = {
        "setup_tool_policy": {
            "mode": "allow-selected",
            "approved_commands": ["uvx ruff --version", "uvx pyrefly --version"],
        }
    }
    policy = resolve_setup_tool_policy(previous_state=previous_state, requested_mode=None, reset_approvals=True)
    assert policy["mode"] == "allow-selected"
    assert policy["approved_commands"] == []


def test_resolve_setup_tool_policy_defaults_to_allow_selected() -> None:
    policy = resolve_setup_tool_policy(previous_state={}, requested_mode=None, reset_approvals=False)
    assert policy["mode"] == "allow-selected"
    assert policy["approved_commands"] == []


def test_resolve_setup_tool_policy_overrides_mode_from_cli() -> None:
    previous_state = {"setup_tool_policy": {"mode": "allow-selected", "approved_commands": ["uvx ruff --version"]}}
    policy = resolve_setup_tool_policy(previous_state=previous_state, requested_mode="prompt", reset_approvals=False)
    assert policy["mode"] == "prompt"
    assert policy["approved_commands"] == ["uvx ruff --version"]


def test_run_selected_tool_setup_shows_live_spinner_feedback_when_interactive(monkeypatch) -> None:
    calls: list[tuple[str, str, str | None]] = []

    def fake_spinner(command: str, *, cwd=None, phase: str = "setup", tool_id: str | None = None):
        calls.append((command, phase, tool_id))
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    # Pre-flight probe runs non-interactively; return failure so setup is not skipped
    monkeypatch.setattr(
        "code_review.review_planner.init._run_shell_command",
        lambda command, **_: SimpleNamespace(returncode=1, stderr="", stdout=""),
    )
    monkeypatch.setattr("code_review.review_planner.init._run_shell_command_with_spinner", fake_spinner)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    results, error = run_selected_tool_setup(
        deterministic_gates=[
            {
                "id": "python-ruff",
                "title": "Ruff",
                "setup": ["macOS: brew install ruff"],
                "commands": ["uvx ruff --version"],
            }
        ],
        approval_policy={"mode": "allow-selected", "approved_commands": []},
        interactive=True,
    )

    assert error is None
    assert results[0]["status"] == "passed"
    assert calls == [
        ("brew install ruff", "install", "python-ruff"),
        ("uvx ruff --version", "setup", "python-ruff"),
    ]


def test_run_selected_tool_setup_skips_install_when_already_installed(monkeypatch) -> None:
    commands: list[str] = []

    # Verify command succeeds on pre-flight probe → tool already installed, setup skipped
    monkeypatch.setattr(
        "code_review.review_planner.init._run_shell_command",
        lambda command, **_: (commands.append(command) or SimpleNamespace(returncode=0, stderr="", stdout="")),
    )
    monkeypatch.setattr("code_review.review_planner.init.platform_label", lambda: "macOS")

    results, error = run_selected_tool_setup(
        deterministic_gates=[
            {
                "id": "js-biome",
                "title": "Biome",
                "setup": ["npm i -D @biomejs/biome"],
                "commands": ["npx @biomejs/biome --version"],
            }
        ],
        approval_policy={"mode": "allow-selected", "approved_commands": ["npm i -D @biomejs/biome"]},
    )

    assert error is None
    assert results[0]["status"] == "passed"
    # Only the pre-flight probe should have run; install command must not appear
    assert commands == ["npx @biomejs/biome --version"]
    assert results[0]["steps"][0]["text"] == "already-installed"


def test_tool_setup_feedback_reports_failed_gate() -> None:
    results = [
        {
            "id": "python-ruff",
            "status": "failed",
            "steps": [
                {"kind": "prereq", "text": "uv installed", "status": "passed"},
                {"kind": "verify", "text": "uvx ruff --version", "status": "failed"},
            ],
        }
    ]

    actions, feedback = tool_setup_feedback(results, "Tool verification failed for python-ruff: uvx ruff --version")

    assert actions[0]["id"] == "deterministic-tool-gate-failed"
    assert "uvx ruff --version" in actions[0]["action"]
    assert feedback == [
        "[P1] Deterministic tool gate failed for python-ruff — Action: Fix `uvx ruff --version` and rerun the selected deterministic gate."
    ]


def test_run_deterministic_gates_executes_review_commands_in_target(tmp_path: Path, monkeypatch) -> None:
    commands: list[tuple[str, str | None]] = []

    def fake_run_shell(command: str, *, cwd: Path | None = None) -> SimpleNamespace:
        commands.append((command, str(cwd) if cwd is not None else None))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("code_review.review_planner.init._run_shell_command", fake_run_shell)

    results, error = run_deterministic_gates(
        target=tmp_path,
        deterministic_gates=[
            {
                "id": "python-ruff",
                "title": "Ruff",
                "review_commands": ["uvx ruff check ."],
                "commands": ["uvx ruff --version"],
            }
        ],
    )

    assert error is None
    assert results[0]["status"] == "passed"
    assert commands == [("uvx ruff check .", str(tmp_path))]


def test_run_deterministic_gates_strips_gitleaks_log_opts_without_parent_commit(tmp_path: Path, monkeypatch) -> None:
    commands: list[str] = []

    def fake_run(command, check=False, capture_output=False, text=False, cwd=None, env=None):  # noqa: ANN001
        if command[:3] == ["git", "-C", str(tmp_path)] and command[3:] == ["rev-parse", "--verify", "HEAD~1"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_run_shell(command: str, *, cwd: Path | None = None, env: dict[str, str] | None = None) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("code_review.review_planner.init._run_shell_command", fake_run_shell)

    results, error = run_deterministic_gates(
        target=tmp_path,
        deterministic_gates=[
            {
                "id": "security-gitleaks",
                "title": "Gitleaks",
                "review_commands": ["gitleaks detect --no-banner --source . --log-opts HEAD~1..HEAD"],
            }
        ],
    )

    assert error is None
    assert results[0]["steps"][0]["text"] == "gitleaks detect --no-banner --source ."
    assert commands == ["gitleaks detect --no-banner --source ."]


def test_run_deterministic_gates_uses_merge_base_range_for_gitleaks(tmp_path: Path, monkeypatch) -> None:
    commands: list[str] = []

    def fake_run(command, check=False, capture_output=False, text=False, cwd=None, env=None):  # noqa: ANN001
        if command[:3] != ["git", "-C", str(tmp_path)]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[3:] == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]:
            return SimpleNamespace(returncode=0, stdout="origin/main\n", stderr="")
        if command[3:] == ["merge-base", "origin/main", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="abc123\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    def fake_run_shell(command: str, *, cwd: Path | None = None, env: dict[str, str] | None = None) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("code_review.review_planner.init._run_shell_command", fake_run_shell)

    results, error = run_deterministic_gates(
        target=tmp_path,
        deterministic_gates=[
            {
                "id": "security-gitleaks",
                "title": "Gitleaks",
                "review_commands": ["gitleaks detect --no-banner --source . --log-opts HEAD~1..HEAD"],
            }
        ],
    )

    assert error is None
    assert results[0]["steps"][0]["text"] == "gitleaks detect --no-banner --source . --log-opts abc123..HEAD"
    assert commands == ["gitleaks detect --no-banner --source . --log-opts abc123..HEAD"]


def test_run_deterministic_gates_falls_back_to_head_range_without_merge_base(tmp_path: Path, monkeypatch) -> None:
    commands: list[str] = []

    def fake_run(command, check=False, capture_output=False, text=False, cwd=None, env=None):  # noqa: ANN001
        if command[:3] != ["git", "-C", str(tmp_path)]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[3:] == ["rev-parse", "--verify", "HEAD~1"]:
            return SimpleNamespace(returncode=0, stdout="parent\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    def fake_run_shell(command: str, *, cwd: Path | None = None, env: dict[str, str] | None = None) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("code_review.review_planner.init._run_shell_command", fake_run_shell)

    results, error = run_deterministic_gates(
        target=tmp_path,
        deterministic_gates=[
            {
                "id": "security-gitleaks",
                "title": "Gitleaks",
                "review_commands": ["gitleaks detect --no-banner --source . --log-opts HEAD~1..HEAD"],
            }
        ],
    )

    assert error is None
    assert results[0]["steps"][0]["text"] == "gitleaks detect --no-banner --source . --log-opts HEAD~1..HEAD"
    assert commands == ["gitleaks detect --no-banner --source . --log-opts HEAD~1..HEAD"]


def test_run_deterministic_gates_passes_command_environment(tmp_path: Path, monkeypatch) -> None:
    env_values: list[dict[str, str] | None] = []

    def fake_run_shell(command: str, *, cwd: Path | None = None, env: dict[str, str] | None = None) -> SimpleNamespace:
        env_values.append(env)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("code_review.review_planner.init._run_shell_command", fake_run_shell)

    results, error = run_deterministic_gates(
        target=tmp_path,
        deterministic_gates=[{"id": "python-ruff", "title": "Ruff", "review_commands": ["uvx ruff check ."]}],
        command_environment={"HOME": "/tmp/code-review-sandbox-home"},
    )

    assert error is None
    assert results[0]["status"] == "passed"
    assert env_values == [{"HOME": "/tmp/code-review-sandbox-home"}]


def test_run_deterministic_gates_uses_spinner_when_interactive(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, str, str | None, str | None]] = []

    def fake_spinner(command: str, *, cwd=None, phase: str = "setup", tool_id: str | None = None):
        calls.append((command, str(cwd) if cwd is not None else None, phase, tool_id))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("code_review.review_planner.init._run_shell_command_with_spinner", fake_spinner)
    results, error = run_deterministic_gates(
        target=tmp_path,
        deterministic_gates=[{"id": "python-ruff", "title": "Ruff", "review_commands": ["uvx ruff check ."]}],
        interactive=True,
    )

    assert error is None
    assert results[0]["status"] == "passed"
    assert calls == [("uvx ruff check .", str(tmp_path), "review", "python-ruff")]


def test_tool_review_feedback_reports_failed_gate() -> None:
    results = [
        {
            "id": "python-bandit",
            "status": "failed",
            "steps": [
                {"kind": "review", "text": "uvx bandit -r src", "status": "failed"},
            ],
        }
    ]

    actions, feedback = tool_review_feedback(results, "Tool review failed for python-bandit: uvx bandit -r src")

    assert actions[0]["id"] == "deterministic-review-gate-failed"
    assert "uvx bandit -r src" in actions[0]["action"]
    assert feedback == [
        "[P1] Review gate failed for python-bandit — Action: Fix `uvx bandit -r src` and rerun the selected review gate."
    ]


def test_compute_deselections_tracks_best_practices_and_tools() -> None:
    previous = {
        "selections": {
            "baselines": ["methodology-core", "review-quality-gates"],
            "languages": ["python", "shell"],
            "specialties": ["cdk"],
            "tools": ["python-ruff", "shell-shellcheck"],
        }
    }
    current = {
        "selections": {
            "baselines": ["methodology-core"],
            "languages": ["python"],
            "specialties": [],
            "tools": ["python-ruff"],
        }
    }
    deselections = compute_deselections(previous_state=previous, current_plan=current)
    assert deselections["baselines"] == ["review-quality-gates"]
    assert deselections["languages"] == ["shell"]
    assert deselections["specialties"] == ["cdk"]
    assert deselections["tools"] == ["shell-shellcheck"]


def test_render_deselection_summary_table() -> None:
    rendered = render_deselection_summary({"baselines": ["review-quality-gates"], "tools": ["js-biome"]})
    assert "Deselection cleanup summary" in rendered
    assert "| Selection area | Deselected values |" in rendered
    assert "baselines" in rendered
    assert "js-biome" in rendered


def test_uninstall_commands_for_tools_returns_known_commands() -> None:
    commands = uninstall_commands_for_tools(tool_ids=["js-biome", "python-ruff"], tools=TOOL_PACKS)
    assert ("js-biome", "npm remove -D @biomejs/biome") in commands
    assert all(tool_id != "python-ruff" for tool_id, _ in commands)


def test_run_uninstall_commands_uses_live_spinner_feedback_when_interactive(monkeypatch) -> None:
    calls: list[tuple[str, str, str | None]] = []

    def fake_spinner(command: str, *, cwd=None, phase: str = "setup", tool_id: str | None = None):
        calls.append((command, phase, tool_id))
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr("code_review.review_planner.init._run_shell_command_with_spinner", fake_spinner)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    results = run_uninstall_commands([("js-biome", "npm remove -D @biomejs/biome")], interactive=True)

    assert results == [("js-biome", "npm remove -D @biomejs/biome", 0)]
    assert calls == [("npm remove -D @biomejs/biome", "uninstall", "js-biome")]


def test_handle_install_or_init_command_prompts_before_uninstall(monkeypatch, tmp_path: Path) -> None:
    prompts: list[str] = []
    runs: list[tuple[list[tuple[str, str]], bool]] = []

    monkeypatch.setattr(
        cli_module, "run_bootstrap_with_status", lambda **kwargs: SimpleNamespace(created=[], updated=[], skipped=[])
    )
    monkeypatch.setattr(cli_module, "merge_learned_extensions", lambda **kwargs: {})
    monkeypatch.setattr(cli_module, "build_dynamic_catalog", lambda _config: ({}, {}, {}, {}, {}, {}))
    monkeypatch.setattr(
        cli_module, "resolve_setup_tool_policy", lambda **kwargs: {"mode": "allow-selected", "approved_commands": []}
    )
    monkeypatch.setattr(cli_module, "state_to_wizard_config", lambda _state: {})
    monkeypatch.setattr(cli_module, "load_state", lambda _path: {})
    monkeypatch.setattr(cli_module, "default_state_path", lambda **kwargs: tmp_path / "state.json")
    monkeypatch.setattr(cli_module, "run_review_wizard", lambda **kwargs: {})
    monkeypatch.setattr(cli_module, "build_plan", lambda **kwargs: {"selections": {"tools": []}})
    monkeypatch.setattr(cli_module, "save_state", lambda **kwargs: None)
    monkeypatch.setattr(cli_module, "run_selected_tool_setup", lambda **kwargs: ([], None))
    monkeypatch.setattr(cli_module, "show_setup_summary", lambda **kwargs: None)
    monkeypatch.setattr(cli_module, "compute_deselections", lambda **kwargs: {"tools": ["js-biome"]})
    monkeypatch.setattr(
        cli_module, "uninstall_commands_for_tools", lambda **kwargs: [("js-biome", "npm remove -D @biomejs/biome")]
    )
    monkeypatch.setattr(cli_module, "render_deselection_summary", lambda *_args, **_kwargs: "deselections")
    monkeypatch.setattr(cli_module, "render_uninstall_commands", lambda *_args, **_kwargs: "uninstall commands")
    monkeypatch.setattr(cli_module, "should_start_review_after_init", lambda **kwargs: False)
    monkeypatch.setattr(
        cli_module,
        "run_uninstall_commands",
        lambda commands, interactive=False: (
            runs.append((commands, interactive)) or [("js-biome", "npm remove -D @biomejs/biome", 0)]
        ),
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "y")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    args = SimpleNamespace(
        target=tmp_path,
        name="code-review",
        harness="copilot",
        state_file=None,
        emit="markdown",
        preview=False,
        no_wizard=False,
        tool_approval=None,
        reset_tool_approvals=False,
        command="init",
        uninstall_deselected_tools=True,
        apply_uninstall_deselected_tools=True,
        post_init_action="exit",
        post_review_action="skip",
    )

    exit_code = cli_module.handle_install_or_init_command(args)

    assert exit_code == 0
    assert prompts == ["Run uninstall commands for deselected tools? [y/N]: "]
    assert runs == [([("js-biome", "npm remove -D @biomejs/biome")], True)]


def test_handle_install_or_init_command_pr_workflow_defaults_to_comment(monkeypatch, tmp_path: Path) -> None:
    captured_post_actions: list[str] = []
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr(
        cli_module, "run_bootstrap_with_status", lambda **kwargs: SimpleNamespace(created=[], updated=[], skipped=[])
    )
    monkeypatch.setattr(cli_module, "merge_learned_extensions", lambda **kwargs: {})
    monkeypatch.setattr(cli_module, "build_dynamic_catalog", lambda _config: ({}, {}, {}, {}, {}, {}))
    monkeypatch.setattr(
        cli_module, "resolve_setup_tool_policy", lambda **kwargs: {"mode": "allow-selected", "approved_commands": []}
    )
    monkeypatch.setattr(cli_module, "state_to_wizard_config", lambda _state: {})
    monkeypatch.setattr(cli_module, "load_state", lambda _path: {})
    monkeypatch.setattr(cli_module, "default_state_path", lambda **kwargs: tmp_path / "state.json")
    monkeypatch.setattr(cli_module, "run_review_wizard", lambda **kwargs: {})
    monkeypatch.setattr(
        cli_module, "build_plan", lambda **kwargs: {"selections": {"tools": []}, "deterministic_gates": []}
    )
    monkeypatch.setattr(cli_module, "save_state", lambda **kwargs: None)
    monkeypatch.setattr(cli_module, "run_selected_tool_setup", lambda **kwargs: ([], None))
    monkeypatch.setattr(cli_module, "show_setup_summary", lambda **kwargs: None)
    monkeypatch.setattr(cli_module, "compute_deselections", lambda **kwargs: {"tools": []})
    monkeypatch.setattr(cli_module, "should_start_review_after_init", lambda **kwargs: True)
    monkeypatch.setattr(cli_module, "choose_review_workflow_after_init", lambda **kwargs: "pr-review")
    monkeypatch.setattr(cli_module, "choose_pull_request_reference", lambda **kwargs: "42")
    monkeypatch.setattr(cli_module, "_run_review_effects", lambda **kwargs: kwargs["plan"])

    def fake_finalize(*, plan, review_target, args):
        captured_post_actions.append(args.post_review_action)
        return 0

    monkeypatch.setattr(cli_module, "_finalize_review_output", fake_finalize)

    args = SimpleNamespace(
        target=tmp_path,
        name="code-review",
        harness="copilot",
        state_file=None,
        emit="markdown",
        preview=False,
        no_wizard=False,
        tool_approval=None,
        reset_tool_approvals=False,
        command="init",
        uninstall_deselected_tools=False,
        apply_uninstall_deselected_tools=False,
        post_init_action="ask",
        post_init_workflow="ask",
        post_review_action="ask",
    )

    exit_code = cli_module.handle_install_or_init_command(args)
    assert exit_code == 0
    assert captured_post_actions == ["comment"]


def test_handle_run_command_executes_review_in_json_mode(monkeypatch, tmp_path: Path) -> None:
    calls = {"bootstrap": 0, "review": 0}
    captured_review_args: list[SimpleNamespace] = []
    monkeypatch.setattr(cli_module, "default_state_path", lambda **kwargs: tmp_path / "state.json")
    monkeypatch.setattr(
        cli_module,
        "resolve_setup_tool_policy",
        lambda **kwargs: {"mode": "prompt", "approved_commands": []},
    )
    monkeypatch.setattr(
        cli_module, "apply_bootstrap", lambda **kwargs: calls.__setitem__("bootstrap", calls["bootstrap"] + 1)
    )
    monkeypatch.setattr(
        cli_module,
        "handle_review_command",
        lambda args: captured_review_args.append(args) or calls.__setitem__("review", calls["review"] + 1) or 0,
    )

    args = SimpleNamespace(
        target=tmp_path,
        name="code-review",
        harness="copilot",
        state_file=None,
        tool_approval="prompt",
        reset_tool_approvals=False,
        emit="json",
        verbose=1,
        provider="auto",
        execution_plugin="shell-local",
        execution_fallback_plugin="",
        sandbox_plugin="scratch-home",
        sandbox_fallback_plugin="passthrough",
        governance_plugin="strict-human-approval",
        pr="123",
        post_review_action="comment",
    )

    exit_code = cli_module.handle_run_command(args)

    assert exit_code == 0
    assert calls == {"bootstrap": 1, "review": 1}
    assert len(captured_review_args) == 1
    assert captured_review_args[0].target == tmp_path
    assert captured_review_args[0].emit == "json"
    assert captured_review_args[0].pr == "123"
    assert captured_review_args[0].setup_tool_policy["mode"] == "prompt"


def test_record_learned_practices_persists_repo_pack(tmp_path: Path) -> None:
    payload = record_learned_practices(
        target=tmp_path,
        practices=["Prefer explicit empty-state copy in TUI screens."],
    )
    learned_path = default_learned_practices_path(target=tmp_path)
    assert learned_path.exists()
    practices = payload["extensions"]["specialties"]["repo-learnings"]["practices"]
    assert "Prefer explicit empty-state copy in TUI screens." in practices


def test_merge_learned_extensions_adds_repo_specialty_pack(tmp_path: Path) -> None:
    record_learned_practices(
        target=tmp_path,
        practices=["Use concise action labels in menu footers."],
    )
    merged = merge_learned_extensions(config={}, target=tmp_path)
    assert "extensions" in merged
    assert "specialties" in merged["extensions"]
    assert "repo-learnings" in merged["extensions"]["specialties"]


def test_write_feedback_report_persists_actions(tmp_path: Path) -> None:
    plan = {
        "selections": {"personas": ["correctness"]},
        "tool_setup_results": [{"id": "python-ruff", "status": "passed", "steps": []}],
        "tool_setup_error": None,
        "findings": [{"file": "src/main.py", "line": 10, "issue": "x", "recommendation": "y", "severity": "important"}],
        "feedback_actions": [{"id": "x", "priority": "P3", "title": "T", "action": "A", "why": "W"}],
        "feedback": ["[P3] T — Action: A"],
    }
    report_path = write_feedback_report(target=tmp_path, plan=plan)
    assert report_path == default_feedback_report_path(target=tmp_path)
    assert report_path.exists()
    payload = report_path.read_text(encoding="utf-8")
    assert '"feedback_actions"' in payload
    assert '"findings"' in payload
    assert '"generated_at"' in payload
    assert '"tool_setup_results"' in payload
    assert '"tool_review_results"' in payload


def test_legacy_script_entrypoint_works_without_pythonpath(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts/main.py"
    result = subprocess.run(
        ["python3", str(script_path), "--help"],
        cwd=tmp_path,
        env={**os.environ},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Compose a multi-persona code review plan" in result.stdout


def test_parse_args_supports_repeatable_and_comma_separated_selections() -> None:
    args = cli_module.parse_args(
        [
            "review",
            ".",
            "--tools",
            "python-ruff",
            "--tools",
            "js-biome,security-semgrep",
            "--strategies",
            "adversarial",
            "--strategies",
            "failure-mode",
        ]
    )
    cli_inputs = cli_module._build_cli_inputs(args)
    assert cli_inputs["tools"] == ["python-ruff", "js-biome", "security-semgrep"]
    assert cli_inputs["strategies"] == ["adversarial", "failure-mode"]


def test_parse_args_does_not_swallow_target_with_tools_flag() -> None:
    args = cli_module.parse_args(["review", "--tools", "python-ruff", "."])
    assert args.target == Path(".")
    cli_inputs = cli_module._build_cli_inputs(args)
    assert cli_inputs["tools"] == ["python-ruff"]


def test_ado_provider_omits_pr_thread_context_without_change_tracking(monkeypatch, tmp_path: Path) -> None:
    provider = AdoPrProvider()
    posted_payloads: list[dict] = []

    monkeypatch.setattr(provider, "_latest_iteration_id", lambda **_kwargs: 7)
    monkeypatch.setattr(provider, "_change_tracking_by_path", lambda **_kwargs: {})

    def fake_run(command, cwd=None, check=False, capture_output=False, text=False):  # noqa: ANN001
        in_file_index = command.index("--in-file")
        payload_path = Path(command[in_file_index + 1])
        posted_payloads.append(json.loads(payload_path.read_text(encoding="utf-8")))
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    message = provider.publish_comment(
        review_target=tmp_path,
        pull_request={
            "number": 100,
            "provider": "ado",
            "project": "SampleProject",
            "repository_id": "repo-id-1",
        },
        body="summary",
        line_comments=[{"file_path": "/src/main.py", "line": 12, "content": "line comment"}],
    )

    assert message == "Posted actionable PR comment to ADO PR #100."
    assert len(posted_payloads) == 2
    assert "pullRequestThreadContext" not in posted_payloads[1]


def test_parse_args_accepts_list_catalog_without_target() -> None:
    args = cli_module.parse_args(["review", "--list-catalog"])
    assert args.list_catalog is True
    assert isinstance(args.target, Path)


def test_resolve_setup_tool_policy_accepts_auto_alias() -> None:
    policy = resolve_setup_tool_policy(previous_state={}, requested_mode="auto")
    assert policy["mode"] == "allow-selected"


def test_run_bootstrap_with_status_prints_summary_when_non_interactive(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    run_bootstrap_with_status(target=tmp_path, harness="copilot", name="code-review")
    output = capsys.readouterr().out
    assert "crk bootstrap" in output


def test_review_cli_attaches_tool_evidence_to_units(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            "uv",
            "run",
            "code-review",
            "review",
            str(tmp_path),
            "--emit",
            "json",
            "--personas",
            "correctness",
            "--baselines",
            "methodology-core",
            "--languages",
            "python",
            "--specialties",
            "ui-ux-cli-tui",
            "--tools",
            "python-ruff",
        ],
        cwd=repo_root,
        env={**os.environ},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["tool_evidence"][0]["phase"] == "setup"
    assert plan["tool_evidence"][-1]["phase"] == "review"
    assert plan["units"][0]["tool_evidence"][0]["phase"] == "setup"
    assert plan["units"][0]["tool_evidence"][-1]["phase"] == "review"
    assert plan["units"][0]["token_strategy"]["toon"] is True
    assert "python-ruff" in plan["units"][0]["prompt_context"]


def test_run_review_effects_removes_placeholder_feedback_when_gates_pass(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module,
        "run_selected_tool_setup",
        lambda deterministic_gates: ([{"id": "python-ruff", "status": "passed", "steps": []}], None),
    )
    monkeypatch.setattr(
        cli_module,
        "run_deterministic_gates",
        lambda target, deterministic_gates, interactive=None: (
            [{"id": "python-ruff", "status": "passed", "steps": []}],
            None,
        ),
    )

    plan = {
        "deterministic_gates": [{"id": "python-ruff"}],
        "feedback_actions": [
            {
                "id": "feedback-loop-next-step",
                "priority": "P3",
                "title": "Feedback loop next step is ready",
                "action": "Run one review cycle.",
                "why": "The plan shape is healthy.",
            }
        ],
        "feedback": ["[P3] Feedback loop next step is ready — Action: Run one review cycle."],
        "units": [
            {
                "persona_title": "Correctness Reviewer",
                "persona_goal": "Check behavior",
                "checks": [],
                "shared_checks": [],
            }
        ],
    }

    updated = cli_module._run_review_effects(plan=plan, review_target=Path("."))
    assert not any(item.get("id") == "feedback-loop-next-step" for item in updated.get("feedback_actions", []))


def test_run_review_effects_can_skip_setup_rerun(monkeypatch) -> None:
    setup_called = {"value": False}

    def fake_setup(*_args, **_kwargs):
        setup_called["value"] = True
        return [], None

    monkeypatch.setattr(cli_module, "run_selected_tool_setup", fake_setup)
    monkeypatch.setattr(
        cli_module,
        "run_deterministic_gates",
        lambda target, deterministic_gates, interactive=None: (
            [{"id": "python-ruff", "status": "passed", "steps": []}],
            None,
        ),
    )
    plan = {"deterministic_gates": [{"id": "python-ruff"}], "feedback_actions": [], "feedback": [], "units": []}
    updated = cli_module._run_review_effects(
        plan=plan,
        review_target=Path("."),
        run_setup=False,
        setup_results=[{"id": "python-ruff", "status": "passed", "steps": []}],
        setup_error=None,
    )
    assert setup_called["value"] is False
    assert updated["tool_setup_results"][0]["id"] == "python-ruff"


def test_run_review_effects_prints_progress_status_when_enabled(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli_module,
        "run_selected_tool_setup",
        lambda deterministic_gates: ([{"id": "python-ruff", "status": "passed", "steps": []}], None),
    )
    monkeypatch.setattr(
        cli_module,
        "run_deterministic_gates",
        lambda target, deterministic_gates, interactive=None: (
            [{"id": "python-ruff", "status": "passed", "steps": []}],
            None,
        ),
    )
    plan = {
        "selections": {"personas": ["correctness"], "tools": ["python-ruff"]},
        "deterministic_gates": [{"id": "python-ruff"}],
        "feedback_actions": [],
        "feedback": [],
        "units": [],
    }

    cli_module._run_review_effects(plan=plan, review_target=Path("."), show_progress=True)
    output = capsys.readouterr().out

    assert "Starting crk workflow" in output
    assert "Running deterministic review gates ----" in output
    assert "Running deterministic review gates" in output


def test_render_gate_failure_summary_includes_failed_command() -> None:
    plan = {
        "tool_review_results": [
            {
                "id": "complexity-lizard",
                "status": "failed",
                "steps": [{"kind": "review", "text": "uvx lizard src -C 50 -L 250", "status": "failed"}],
            }
        ]
    }
    rendered = cli_module._render_gate_failure_summary(plan)
    assert "Gate failures" in rendered
    assert "complexity-lizard (review)" in rendered
    assert "uvx lizard src -C 50 -L 250" in rendered


def test_render_completion_summary_includes_counts() -> None:
    plan = {
        "units": [{}, {}],
        "feedback_actions": [
            {"id": "a", "priority": "P1", "title": "blocking"},
            {"id": "b", "priority": "P2", "title": "important"},
        ],
        "feedback_report": {"path": "/tmp/report.json"},
        "feedback_state": {"path": "/tmp/state.json"},
        "tool_review_results": [],
    }
    rendered = cli_module._render_completion_summary(plan, post_action="plan")
    assert "Review finished" in rendered
    assert "units: 2" in rendered
    assert "findings: 2" in rendered
    assert "blocking: 1" in rendered
    assert "plan mode" in rendered


def test_render_compact_review_output_mentions_verbose_option() -> None:
    rendered = cli_module._render_compact_review_output(
        {
            "target": "/repo",
            "review_workflow": "dev-loop",
            "selections": {"personas": ["correctness"], "tools": ["python-ruff"], "languages": ["python"]},
            "feedback_actions": [{"priority": "P2", "title": "Improve boundary validation", "action": "Add guard"}],
        }
    )
    assert "crk summary" in rendered
    assert "Use -vvv for full plan details." in rendered


def test_finalize_review_output_prints_completion_summary(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(cli_module, "write_feedback_report", lambda target, plan: tmp_path / "latest.json")
    monkeypatch.setattr(cli_module, "update_feedback_state", lambda target, plan: tmp_path / "state.json")
    monkeypatch.setattr(cli_module, "write_feedback_learning_queue", lambda target: tmp_path / "learning-queue.json")
    monkeypatch.setattr(cli_module, "to_markdown", lambda plan: "# plan")
    monkeypatch.setattr(
        cli_module,
        "_detect_active_pull_request",
        lambda review_target, pr_ref=None, provider_preference="auto": None,
    )
    monkeypatch.setattr(cli_module, "_resolve_post_review_action", lambda requested, active_pr: "plan")

    args = SimpleNamespace(emit="markdown", feedback_status=[], post_review_action="plan")
    exit_code = cli_module._finalize_review_output(
        plan={"feedback_actions": [], "tool_review_results": [], "units": []}, review_target=tmp_path, args=args
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "---- Complete ----" in output
    assert "Implementation plan is captured in the review output above." in output


def test_apply_requirements_walkthrough_uses_grilling_refiner(monkeypatch, tmp_path: Path) -> None:
    called = {"value": False}

    def fake_refiner(*, requirements):
        called["value"] = True
        return {**requirements, "requirements_refiner": "grilling", "walkthrough_confirmed": True}

    monkeypatch.setattr(
        cli_module,
        "derive_requirements",
        lambda **_kwargs: {
            "requirements": [{"id": 1, "text": "A", "source": "docs", "confidence": "medium"}],
            "notes": [],
        },
    )
    monkeypatch.setattr(cli_module, "apply_grilling_refinement", fake_refiner)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    args = SimpleNamespace(
        requirements_issue=None,
        issue_provider="auto",
        requirements="",
        requirements_walkthrough=True,
        requirements_refiner="grilling",
        emit="markdown",
    )
    updated = cli_module._apply_requirements_walkthrough(args=args, review_target=tmp_path, plan={})
    assert called["value"] is True
    assert updated["requirements_compliance"]["requirements_refiner"] == "grilling"


def test_preflight_review_scope_accepts_empty_base_ref(tmp_path: Path) -> None:
    scope = cli_module._preflight_review_scope(review_target=tmp_path, base_ref="")
    assert scope["mode"] == "repo-current"
    assert scope["base_ref"] is None


def test_resolve_target_path_uses_launch_pwd_for_relative_paths(monkeypatch, tmp_path: Path) -> None:
    launch_dir = tmp_path / "launch"
    run_dir = tmp_path / "run"
    launch_dir.mkdir()
    run_dir.mkdir()
    monkeypatch.chdir(run_dir)
    monkeypatch.setenv("PWD", str(launch_dir))
    resolved = cli_module._resolve_target_path(Path("."))
    assert resolved == launch_dir.resolve()


def test_preflight_review_scope_rejects_empty_diff(monkeypatch, tmp_path: Path) -> None:
    calls = {"index": 0}

    def fake_run(_command, check, capture_output, text):
        calls["index"] += 1
        if calls["index"] == 1:
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    try:
        cli_module._preflight_review_scope(review_target=tmp_path, base_ref="main")
        raise AssertionError("Expected ValueError for empty diff scope")
    except ValueError as error:
        assert "has no changed files" in str(error)


def test_feedback_state_tracks_status_and_active_context(tmp_path: Path) -> None:
    plan = {
        "feedback_actions": [
            {"id": "missing-language-pack", "priority": "P2", "title": "x", "action": "Select language", "why": "y"}
        ]
    }
    state_path = update_feedback_state(target=tmp_path, plan=plan)
    assert state_path == default_feedback_state_path(target=tmp_path)
    payload = state_path.read_text(encoding="utf-8")
    assert '"status": "open"' in payload
    assert '"active_context"' in payload

    apply_feedback_status_updates(target=tmp_path, updates=["missing-language-pack:in_progress"])
    updated_payload = state_path.read_text(encoding="utf-8")
    assert '"status": "in_progress"' in updated_payload
    assert '"active_context"' in updated_payload
    assert '"status": "in_progress"' in updated_payload.split('"active_context"')[1]


def test_write_feedback_learning_queue_includes_open_in_progress_and_accepted_items(tmp_path: Path) -> None:
    plan = {
        "feedback_actions": [
            {"id": "p1-open", "priority": "P1", "title": "Open item", "action": "Fix open item", "why": "risk"},
            {"id": "p2-open", "priority": "P2", "title": "Second item", "action": "Fix second item", "why": "risk"},
        ]
    }
    update_feedback_state(target=tmp_path, plan=plan)
    apply_feedback_status_updates(target=tmp_path, updates=["p2-open:accepted", "p1-open:in_progress"])

    queue_path = write_feedback_learning_queue(target=tmp_path)

    assert queue_path == default_feedback_learning_queue_path(target=tmp_path)
    payload = json.loads(queue_path.read_text(encoding="utf-8"))
    assert [item["id"] for item in payload["items"]] == ["p1-open", "p2-open"]
    assert payload["items"][0]["status"] == "in_progress"
    assert payload["items"][1]["status"] == "accepted"


def test_promote_accepted_feedback_to_learnings_marks_items_done(tmp_path: Path) -> None:
    plan = {
        "feedback_actions": [
            {
                "id": "accepted-item",
                "priority": "P1",
                "title": "Accepted",
                "action": "Use tighter diff scoping",
                "why": "noise",
            },
            {"id": "open-item", "priority": "P2", "title": "Open", "action": "Keep open", "why": "later"},
        ]
    }
    update_feedback_state(target=tmp_path, plan=plan)
    apply_feedback_status_updates(target=tmp_path, updates=["accepted-item:accepted"])

    promotion = promote_accepted_feedback_to_learnings(target=tmp_path)

    assert promotion["promoted_ids"] == ["accepted-item"]
    learned = json.loads(default_learned_practices_path(target=tmp_path).read_text(encoding="utf-8"))
    practices = learned["extensions"]["specialties"]["repo-learnings"]["practices"]
    assert "Use tighter diff scoping" in practices

    state = json.loads(default_feedback_state_path(target=tmp_path).read_text(encoding="utf-8"))
    assert state["items"]["accepted-item"]["status"] == "done"
    assert state["items"]["accepted-item"]["active"] is False
