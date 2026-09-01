from __future__ import annotations

import argparse
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

from .review_planner.catalog import build_dynamic_catalog
from .review_planner.init import (
    apply_bootstrap,
    apply_feedback_status_updates,
    apply_uninstall,
    choose_pull_request_reference,
    choose_review_workflow_after_init,
    colorize_console_block,
    compute_deselections,
    default_feedback_learning_queue_path,
    default_state_path,
    extract_state_payload,
    load_state,
    pause_for_acknowledgement,
    promote_accepted_feedback_to_learnings,
    render_deselection_summary,
    render_init_plan,
    render_uninstall_commands,
    render_uninstall_result,
    resolve_setup_tool_policy,
    run_bootstrap_with_status,
    run_deterministic_gates,
    run_selected_tool_setup,
    run_uninstall_commands,
    save_state,
    should_start_review_after_init,
    state_to_wizard_config,
    tool_review_feedback,
    tool_setup_feedback,
    uninstall_commands_for_tools,
    update_feedback_state,
    write_feedback_learning_queue,
    write_feedback_report,
)
from .review_planner.io_utils import load_json
from .review_planner.learning import merge_learned_extensions, record_learned_practices
from .review_planner.migration import CURRENT_SCHEMA_VERSION, migrate_config_payload
from .review_planner.planner import (
    attach_tool_evidence_to_units,
    build_plan,
    build_tool_evidence,
    build_unit_prompt_context,
    parse_csv,
)
from .review_planner.plugins.execution import build_default_execution_registry
from .review_planner.plugins.governance import build_default_governance_registry
from .review_planner.plugins.providers import build_default_provider_registry, extract_ado_work_item_id
from .review_planner.plugins.sandbox import build_default_sandbox_registry
from .review_planner.render import to_catalog_markdown, to_markdown
from .review_planner.requirements import apply_grilling_refinement, apply_walkthrough_overrides, derive_requirements
from .review_planner.token_strategy import TOKEN_PROFILES, resolve_token_policy
from .review_planner.tui import run_review_wizard, show_setup_summary


def _add_arguments(parser: argparse.ArgumentParser, items: list[tuple[str, dict]]) -> None:
    for args, kwargs in items:
        parser.add_argument(args, **kwargs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compose a multi-persona code review plan (code-review; alias: crk).")
    subparsers = parser.add_subparsers(dest="command")

    review = subparsers.add_parser("review", help="Generate a review plan.")
    _add_arguments(
        review,
        [
            (
                "target",
                {
                    "type": Path,
                    "nargs": "?",
                    "default": Path.cwd(),
                    "help": "Target repository path or scope identifier.",
                },
            ),
            ("--config", {"type": Path, "help": "JSON profile with selections and optional extensions."}),
            (
                "--personas",
                {"action": "append", "default": [], "help": "Persona IDs (repeat flag or use comma-separated IDs)."},
            ),
            (
                "--exclude-personas",
                {
                    "action": "append",
                    "default": [],
                    "help": "Persona IDs to exclude (repeat flag or use comma-separated IDs).",
                },
            ),
            (
                "--baselines",
                {
                    "action": "append",
                    "default": [],
                    "help": "Baseline pack IDs (repeat flag or use comma-separated IDs).",
                },
            ),
            (
                "--exclude-baselines",
                {
                    "action": "append",
                    "default": [],
                    "help": "Baseline pack IDs to exclude (repeat flag or use comma-separated IDs).",
                },
            ),
            (
                "--tools",
                {"action": "append", "default": [], "help": "Tool pack IDs (repeat flag or use comma-separated IDs)."},
            ),
            (
                "--exclude-tools",
                {
                    "action": "append",
                    "default": [],
                    "help": "Tool pack IDs to exclude (repeat flag or use comma-separated IDs).",
                },
            ),
            (
                "--languages",
                {
                    "action": "append",
                    "default": [],
                    "help": "Language pack IDs (repeat flag or use comma-separated IDs).",
                },
            ),
            (
                "--exclude-languages",
                {
                    "action": "append",
                    "default": [],
                    "help": "Language pack IDs to exclude (repeat flag or use comma-separated IDs).",
                },
            ),
            (
                "--specialties",
                {
                    "action": "append",
                    "default": [],
                    "help": "Specialty pack IDs (repeat flag or use comma-separated IDs).",
                },
            ),
            (
                "--exclude-specialties",
                {
                    "action": "append",
                    "default": [],
                    "help": "Specialty pack IDs to exclude (repeat flag or use comma-separated IDs).",
                },
            ),
            (
                "--strategies",
                {"action": "append", "default": [], "help": "Strategy IDs (repeat flag or use comma-separated IDs)."},
            ),
            (
                "--exclude-strategies",
                {
                    "action": "append",
                    "default": [],
                    "help": "Strategy IDs to exclude (repeat flag or use comma-separated IDs).",
                },
            ),
            (
                "--strategy-mode",
                {
                    "choices": ["overlay", "fanout"],
                    "default": "overlay",
                    "help": "overlay: same strategies on each persona, fanout: persona x strategy units.",
                },
            ),
            (
                "--token-profile",
                {"choices": sorted(TOKEN_PROFILES), "default": "balanced", "help": "Token optimization preset."},
            ),
            ("--cache-mode", {"choices": ["none", "prompt", "context", "full"], "help": "Caching aggressiveness."}),
            ("--model-routing", {"choices": ["right-size", "fixed"], "help": "Subagent model routing mode."}),
            ("--max-parallel-units", {"type": int, "help": "Max concurrent subagent units."}),
            ("--max-files-per-unit", {"type": int, "help": "Guidance cap for files per unit."}),
            ("--max-file-hints", {"type": int, "help": "Max file hint globs per unit after TOON narrowing."}),
            ("--emit", {"choices": ["markdown", "json"], "default": "markdown", "help": "Output format."}),
            (
                "-v",
                {
                    "dest": "verbose",
                    "action": "count",
                    "default": 0,
                    "help": "Increase verbosity (use -vvv for full plan output).",
                },
            ),
            (
                "--list-catalog",
                {"action": "store_true", "help": "List available personas, packs, strategies, and profiles."},
            ),
            ("--wizard", {"action": "store_true", "help": "Run an interactive TUI wizard to choose review options."}),
            (
                "--requirements-check",
                {
                    "action": "store_true",
                    "help": "Derive requirements from issue/docs/tests/user input and include compliance context in the plan.",
                },
            ),
            ("--requirements-issue", {"help": "Issue reference for requirements derivation (for example: '#123')."}),
            (
                "--issue-provider",
                {
                    "choices": ["auto", "github", "ado", "jira"],
                    "default": "auto",
                    "help": "Issue provider for requirements derivation.",
                },
            ),
            (
                "--requirements",
                {
                    "default": "",
                    "help": "Comma-separated user-provided requirements to include in requirements derivation.",
                },
            ),
            (
                "--requirements-walkthrough",
                {
                    "action": "store_true",
                    "help": "Run an interactive walkthrough to confirm and edit derived requirements.",
                },
            ),
            (
                "--requirements-refiner",
                {
                    "choices": ["manual", "grilling"],
                    "default": "manual",
                    "help": "Interactive requirements refinement mode used with --requirements-walkthrough.",
                },
            ),
            (
                "--base-ref",
                {
                    "default": "",
                    "help": "Optional git base ref (commit/branch/tag) for review-scope preflight via <base>...HEAD.",
                },
            ),
            (
                "--learn-best-practice",
                {
                    "action": "append",
                    "default": [],
                    "help": "Add a learned best-practice item to the repo-local learnings pack (repeatable).",
                },
            ),
            (
                "--feedback-status",
                {
                    "action": "append",
                    "default": [],
                    "help": "Update feedback state using <feedback_id>:<status> (status: open|in_progress|accepted|dismissed|done).",
                },
            ),
            (
                "--provider",
                {
                    "choices": ["auto", "github", "ado", "jira"],
                    "default": "auto",
                    "help": "Preferred provider for integrations and provider registry selection.",
                },
            ),
            (
                "--execution-plugin",
                {
                    "default": "shell-local",
                    "help": "Execution plugin id for setup/review deterministic gate execution.",
                },
            ),
            (
                "--execution-fallback-plugin",
                {"default": "", "help": "Fallback execution plugin id if the primary execution plugin fails."},
            ),
            (
                "--sandbox-plugin",
                {"default": "scratch-home", "help": "Sandbox plugin id for isolated command execution state."},
            ),
            (
                "--sandbox-fallback-plugin",
                {"default": "passthrough", "help": "Fallback sandbox plugin id if the primary sandbox plugin fails."},
            ),
            (
                "--governance-plugin",
                {"default": "strict-human-approval", "help": "Governance plugin id controlling side-effect approvals."},
            ),
            (
                "--learn-accepted-feedback",
                {
                    "action": "store_true",
                    "help": "Promote accepted feedback items into the repo learnings pack and mark them done.",
                },
            ),
            (
                "--learn-feedback-id",
                {
                    "action": "append",
                    "default": [],
                    "help": "Specific feedback id to promote when using --learn-accepted-feedback (repeatable).",
                },
            ),
            (
                "--pr",
                {
                    "default": "",
                    "help": "PR number or URL for PR-focused review/comment publishing.",
                },
            ),
            (
                "--post-review-action",
                {
                    "choices": ["ask", "comment", "plan", "skip"],
                    "default": "ask",
                    "help": "After review, ask to comment on the active PR, publish a comment, generate a plan, or skip the action.",
                },
            ),
            (
                "--harness",
                {
                    "choices": ["copilot", "claude-code", "opencode"],
                    "default": None,
                    "help": "AI harness for persona execution. Persisted in state; prompted interactively when not set.",
                },
            ),
        ],
    )

    install = subparsers.add_parser("install", help="Install repo-local Copilot bootstrap files.")
    _add_arguments(
        install,
        [
            (
                "--harness",
                {"choices": ["copilot", "claude-code", "opencode"], "required": True, "help": "Target harness."},
            ),
            ("--name", {"default": "crk", "help": "Installed command name if you need to rename it."}),
            ("--target", {"type": Path, "default": Path.cwd(), "help": "Repository to bootstrap."}),
            ("--emit", {"choices": ["markdown", "json"], "default": "markdown", "help": "Output format."}),
            (
                "-v",
                {
                    "dest": "verbose",
                    "action": "count",
                    "default": 0,
                    "help": "Increase verbosity (use -vvv for full plan output).",
                },
            ),
            ("--preview", {"action": "store_true", "help": "Show the planned bootstrap without writing files."}),
        ],
    )

    init = subparsers.add_parser("init", help="Initialize the harness-specific command wrapper.")
    _add_arguments(
        init,
        [
            (
                "--harness",
                {"choices": ["copilot", "claude-code", "opencode"], "required": True, "help": "Target harness."},
            ),
            ("--name", {"default": "crk", "help": "Installed command name if you need to rename it."}),
            ("--target", {"type": Path, "default": Path.cwd(), "help": "Repository to bootstrap and review."}),
            ("--state-file", {"type": Path, "help": "Path to persisted wizard selections."}),
            ("--emit", {"choices": ["markdown", "json"], "default": "markdown", "help": "Output format."}),
            ("--preview", {"action": "store_true", "help": "Show the planned bootstrap without writing files."}),
            ("--no-wizard", {"action": "store_true", "help": "Skip launching the interactive wizard after bootstrap."}),
            (
                "--tool-approval",
                {
                    "choices": ["prompt", "allow-selected", "auto"],
                    "help": "Setup command approval mode: prompt per command or auto/allow-selected to auto-approve selected tool commands.",
                },
            ),
            (
                "--reset-tool-approvals",
                {
                    "action": "store_true",
                    "help": "Clear previously approved setup commands before running setup.",
                },
            ),
            (
                "--post-init-action",
                {
                    "choices": ["ask", "start", "exit"],
                    "default": "ask",
                    "help": "After init setup succeeds: ask to start review now, start immediately, or exit.",
                },
            ),
            (
                "--post-init-workflow",
                {
                    "choices": ["ask", "dev-loop", "pr-review"],
                    "default": "ask",
                    "help": "Workflow to run when init starts review: ask, developer loop, or PR review.",
                },
            ),
            (
                "--start-pr-review",
                {
                    "action": "store_true",
                    "help": "Shorthand for --post-init-action start --post-init-workflow pr-review.",
                },
            ),
            (
                "--pr",
                {
                    "default": "",
                    "help": "PR number or URL to use when PR review workflow is selected.",
                },
            ),
            (
                "--post-review-action",
                {
                    "choices": ["ask", "comment", "plan", "skip"],
                    "default": "ask",
                    "help": "After review, ask to comment on the active PR, publish a comment, generate a plan, or skip the action.",
                },
            ),
            (
                "--provider",
                {
                    "choices": ["auto", "github", "ado", "jira"],
                    "default": "auto",
                    "help": "Preferred provider for integrations and provider registry selection.",
                },
            ),
            (
                "--execution-plugin",
                {
                    "default": "shell-local",
                    "help": "Execution plugin id for setup/review deterministic gate execution.",
                },
            ),
            (
                "--execution-fallback-plugin",
                {"default": "", "help": "Fallback execution plugin id if the primary execution plugin fails."},
            ),
            (
                "--sandbox-plugin",
                {"default": "scratch-home", "help": "Sandbox plugin id for isolated command execution state."},
            ),
            (
                "--sandbox-fallback-plugin",
                {"default": "passthrough", "help": "Fallback sandbox plugin id if the primary sandbox plugin fails."},
            ),
            (
                "--governance-plugin",
                {"default": "strict-human-approval", "help": "Governance plugin id controlling side-effect approvals."},
            ),
        ],
    )
    init.add_argument(
        "--uninstall-deselected-tools",
        "--uninstall-deselected-options",
        dest="uninstall_deselected_tools",
        action="store_true",
        help="Show cleanup summary and uninstall commands for deselected best-practices and tool packs.",
    )
    init.add_argument(
        "--apply-uninstall-deselected-tools",
        "--apply-uninstall-deselected-options",
        dest="apply_uninstall_deselected_tools",
        action="store_true",
        help="Execute uninstall commands for deselected tool packs.",
    )

    uninstall = subparsers.add_parser("uninstall", help="Remove repo-local crk bootstrap files.")
    _add_arguments(
        uninstall,
        [
            (
                "--harness",
                {"choices": ["copilot", "claude-code", "opencode"], "required": True, "help": "Target harness."},
            ),
            ("--name", {"default": "crk", "help": "Installed command name."}),
            ("--target", {"type": Path, "default": Path.cwd(), "help": "Repository to remove bootstrap from."}),
            ("--emit", {"choices": ["markdown", "json"], "default": "markdown", "help": "Output format."}),
        ],
    )

    run = subparsers.add_parser("run", help="One-shot init + review workflow.")
    _add_arguments(
        run,
        [
            (
                "target",
                {"type": Path, "nargs": "?", "default": Path.cwd(), "help": "Repository to bootstrap and review."},
            ),
            (
                "--harness",
                {"choices": ["copilot", "claude-code", "opencode"], "default": "copilot", "help": "Target harness."},
            ),
            ("--name", {"default": "crk", "help": "Installed command name if you need to rename it."}),
            ("--state-file", {"type": Path, "help": "Path to persisted wizard selections."}),
            ("--emit", {"choices": ["markdown", "json"], "default": "markdown", "help": "Output format."}),
            (
                "--tool-approval",
                {"choices": ["prompt", "allow-selected", "auto"], "help": "Setup command approval mode."},
            ),
            (
                "--reset-tool-approvals",
                {"action": "store_true", "help": "Clear previously approved setup commands before running setup."},
            ),
            ("--pr", {"default": "", "help": "PR number or URL for PR-focused review/comment publishing."}),
            (
                "--post-review-action",
                {
                    "choices": ["ask", "comment", "plan", "skip"],
                    "default": "ask",
                    "help": "After review, ask to comment, publish comment, generate plan, or skip.",
                },
            ),
            (
                "--provider",
                {
                    "choices": ["auto", "github", "ado", "jira"],
                    "default": "auto",
                    "help": "Preferred provider for integrations and provider registry selection.",
                },
            ),
            (
                "--execution-plugin",
                {
                    "default": "shell-local",
                    "help": "Execution plugin id for setup/review deterministic gate execution.",
                },
            ),
            (
                "--execution-fallback-plugin",
                {"default": "", "help": "Fallback execution plugin id if the primary execution plugin fails."},
            ),
            (
                "--sandbox-plugin",
                {"default": "scratch-home", "help": "Sandbox plugin id for isolated command execution state."},
            ),
            (
                "--sandbox-fallback-plugin",
                {"default": "passthrough", "help": "Fallback sandbox plugin id if the primary sandbox plugin fails."},
            ),
            (
                "--governance-plugin",
                {"default": "strict-human-approval", "help": "Governance plugin id controlling side-effect approvals."},
            ),
            (
                "-v",
                {
                    "dest": "verbose",
                    "action": "count",
                    "default": 0,
                    "help": "Increase verbosity (use -vvv for full plan output).",
                },
            ),
        ],
    )

    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] not in {"review", "install", "init", "uninstall", "run"} and not args[0].startswith("-"):
        args = ["review", *args]
    return build_parser().parse_args(args)


def _resolve_target_path(path: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    launch_cwd_raw = os.environ.get("PWD", "").strip()
    if launch_cwd_raw:
        launch_cwd = Path(launch_cwd_raw).expanduser()
        if launch_cwd.exists():
            return (launch_cwd / path).resolve()
    return path.resolve()


def handle_uninstall_command(args: argparse.Namespace) -> int:
    target = _resolve_target_path(args.target)
    result = apply_uninstall(target=target, name=args.name)
    if args.emit == "json":
        print(
            json.dumps(
                {
                    "harness": args.harness,
                    "command_name": args.name,
                    "target": str(target),
                    "removed": [str(path) for path in result.removed],
                    "skipped": [str(path) for path in result.skipped],
                },
                indent=2,
            ),
        )
        return 0
    print(colorize_console_block(render_uninstall_result(result, harness=args.harness, name=args.name)))
    return 0


def handle_install_or_init_command(args: argparse.Namespace) -> int:
    target = _resolve_target_path(args.target)
    if bool(getattr(args, "start_pr_review", False)):
        args.post_init_action = "start"
        args.post_init_workflow = "pr-review"
    if args.preview:
        rendered = render_init_plan(harness=args.harness, name=args.name, emit=args.emit)
        print(colorize_console_block(rendered) if args.emit == "markdown" else rendered)
        return 0

    if args.emit == "json":
        result = apply_bootstrap(target=target, name=args.name)
    else:
        result = run_bootstrap_with_status(target=target, harness=args.harness, name=args.name)

    if args.emit == "json":
        print(
            json.dumps(
                {
                    "harness": args.harness,
                    "command_name": args.name,
                    "target": str(target),
                    "created": [str(path) for path in result.created],
                    "updated": [str(path) for path in result.updated],
                    "skipped": [str(path) for path in result.skipped],
                },
                indent=2,
            ),
        )
        return 0

    if args.command == "install" or args.no_wizard or not sys.stdin.isatty() or not sys.stdout.isatty():
        if sys.stdin.isatty() and sys.stdout.isatty():
            pause_for_acknowledgement("\nBootstrap complete. You can now open Copilot in this repository.")
        return 0

    runtime_config = merge_learned_extensions(config={}, target=target)
    personas, baselines, tools, languages, specialties, strategies = build_dynamic_catalog(runtime_config)
    balanced_profile = "balanced"
    bootstrap_token_args = argparse.Namespace(
        token_profile=balanced_profile,
        cache_mode=None,
        model_routing=None,
        max_parallel_units=None,
        max_files_per_unit=None,
        max_file_hints=None,
    )
    state_path = _resolve_target_path(args.state_file) if args.state_file else default_state_path(target=target)
    previous_state = load_state(state_path) if state_path.exists() else {}
    setup_tool_policy = resolve_setup_tool_policy(
        previous_state=previous_state,
        requested_mode=getattr(args, "tool_approval", None),
        reset_approvals=bool(getattr(args, "reset_tool_approvals", False)),
    )
    wizard_config = state_to_wizard_config(previous_state)
    selections = run_review_wizard(
        target=str(target),
        personas=personas,
        baselines=baselines,
        tools=tools,
        languages=languages,
        specialties=specialties,
        strategies=strategies,
        config=wizard_config,
    )
    if selections is None:
        return 130
    plan = build_plan(
        target=str(target),
        personas=personas,
        baselines=baselines,
        tools=tools,
        languages=languages,
        specialties=specialties,
        strategies=strategies,
        config=selections,
        cli_inputs={
            "personas": [],
            "exclude_personas": [],
            "baselines": [],
            "exclude_baselines": [],
            "tools": [],
            "exclude_tools": [],
            "languages": [],
            "exclude_languages": [],
            "specialties": [],
            "exclude_specialties": [],
            "strategies": [],
            "exclude_strategies": [],
        },
        token_policy=resolve_token_policy({}, bootstrap_token_args),
        strategy_mode="overlay",
    )
    plan["setup_tool_policy"] = setup_tool_policy
    save_state(
        state_path=state_path, payload=extract_state_payload(harness=args.harness, command_name=args.name, plan=plan)
    )
    sandbox_registry = build_default_sandbox_registry()
    sandbox_plugin = sandbox_registry.resolve(getattr(args, "sandbox_plugin", "scratch-home"))
    try:
        with sandbox_plugin.enter(target=target) as sandbox_session:
            plan["sandbox_plugin"] = sandbox_session.id
            plan["sandbox"] = {"plugin": sandbox_session.id, "note": sandbox_session.note}
            tool_setup_results, tool_setup_error = run_selected_tool_setup(
                deterministic_gates=plan.get("deterministic_gates", []),
                approval_policy=setup_tool_policy,
                command_environment=sandbox_session.environment,
            )
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as primary_error:
        fallback_id = str(getattr(args, "sandbox_fallback_plugin", "passthrough")).strip()
        if fallback_id and fallback_id != sandbox_plugin.id:
            fallback_plugin = sandbox_registry.resolve(fallback_id)
            plan["sandbox_fallback"] = fallback_plugin.id
            with fallback_plugin.enter(target=target) as sandbox_session:
                plan["sandbox_plugin"] = sandbox_session.id
                plan["sandbox"] = {"plugin": sandbox_session.id, "note": sandbox_session.note}
                tool_setup_results, tool_setup_error = run_selected_tool_setup(
                    deterministic_gates=plan.get("deterministic_gates", []),
                    approval_policy=setup_tool_policy,
                    command_environment=sandbox_session.environment,
                )
            _append_feedback_action(
                plan,
                {
                    "id": "sandbox-plugin-fallback",
                    "priority": "P2",
                    "title": "Sandbox plugin fallback used",
                    "action": f"Investigate primary sandbox plugin '{sandbox_plugin.id}' failure.",
                    "why": str(primary_error),
                },
            )
        else:
            raise
    plan["setup_tool_policy"] = setup_tool_policy
    save_state(
        state_path=state_path, payload=extract_state_payload(harness=args.harness, command_name=args.name, plan=plan)
    )
    show_setup_summary(plan=plan, tool_setup_results=tool_setup_results, tool_setup_error=tool_setup_error)
    if tool_setup_error:
        return 1
    deselections = compute_deselections(previous_state=previous_state, current_plan=plan)
    deselected_tools = deselections.get("tools", [])
    if args.uninstall_deselected_tools:
        print(colorize_console_block(render_deselection_summary(deselections)))
        uninstall_commands = uninstall_commands_for_tools(tool_ids=deselected_tools, tools=tools)
        print(colorize_console_block(render_uninstall_commands(uninstall_commands)))
        if args.apply_uninstall_deselected_tools:
            should_uninstall = True
            if sys.stdin.isatty() and sys.stdout.isatty():
                answer = input("Run uninstall commands for deselected tools? [y/N]: ").strip().lower()
                should_uninstall = answer in {"y", "yes"}
            if should_uninstall:
                command_results = run_uninstall_commands(uninstall_commands, interactive=True)
                table = (
                    "| Tool | Command | Exit |\n|---|---|---|\n"
                    + "\n".join(
                        f"| `{tool_id}` | `{command}` | `{code}` |" for tool_id, command, code in command_results
                    )
                    + ("\n" if command_results else "| (none) | - | - |\n")
                )
                print(table)
    if should_start_review_after_init(harness=args.harness, action=args.post_init_action):
        selected_workflow = choose_review_workflow_after_init(
            harness=args.harness,
            workflow=getattr(args, "post_init_workflow", "ask"),
        )
        post_review_action = args.post_review_action
        if selected_workflow == "pr-review":
            plan["review_pr_ref"] = choose_pull_request_reference(provided=getattr(args, "pr", ""))
        if selected_workflow == "pr-review" and post_review_action == "ask":
            post_review_action = "comment"
        plan["review_workflow"] = selected_workflow
        plan["provider_preference"] = getattr(args, "provider", "auto")
        review_args = argparse.Namespace(
            emit=args.emit,
            feedback_status=[],
            learn_accepted_feedback=False,
            learn_feedback_id=[],
            provider=getattr(args, "provider", "auto"),
            governance_plugin=getattr(args, "governance_plugin", "strict-human-approval"),
            execution_plugin=getattr(args, "execution_plugin", "shell-local"),
            execution_fallback_plugin=getattr(args, "execution_fallback_plugin", ""),
            post_review_action=post_review_action,
            verbose=getattr(args, "verbose", 0),
        )
        plan = _run_review_effects(
            plan=plan,
            review_target=target,
            run_setup=False,
            setup_results=tool_setup_results,
            setup_error=tool_setup_error,
            show_progress=args.emit == "markdown" and sys.stdin.isatty() and sys.stdout.isatty(),
            execution_plugin_id=getattr(args, "execution_plugin", "shell-local"),
            execution_fallback_plugin_id=getattr(args, "execution_fallback_plugin", ""),
            sandbox_plugin_id=getattr(args, "sandbox_plugin", "scratch-home"),
            sandbox_fallback_plugin_id=getattr(args, "sandbox_fallback_plugin", "passthrough"),
        )
        return _finalize_review_output(plan=plan, review_target=target, args=review_args)
    return 0


def _build_review_args_from_run_args(*, run_args: argparse.Namespace, target: Path) -> argparse.Namespace:
    return argparse.Namespace(
        command="review",
        target=target,
        config=None,
        personas=[],
        exclude_personas=[],
        baselines=[],
        exclude_baselines=[],
        tools=[],
        exclude_tools=[],
        languages=[],
        exclude_languages=[],
        specialties=[],
        exclude_specialties=[],
        strategies=[],
        exclude_strategies=[],
        strategy_mode="overlay",
        token_profile="balanced",
        cache_mode=None,
        model_routing=None,
        max_parallel_units=None,
        max_files_per_unit=None,
        max_file_hints=None,
        emit=getattr(run_args, "emit", "markdown"),
        verbose=getattr(run_args, "verbose", 0),
        list_catalog=False,
        wizard=False,
        requirements_check=False,
        requirements_issue=None,
        issue_provider="auto",
        requirements="",
        requirements_walkthrough=False,
        requirements_refiner="manual",
        base_ref="",
        learn_best_practice=[],
        feedback_status=[],
        provider=getattr(run_args, "provider", "auto"),
        execution_plugin=getattr(run_args, "execution_plugin", "shell-local"),
        execution_fallback_plugin=getattr(run_args, "execution_fallback_plugin", ""),
        sandbox_plugin=getattr(run_args, "sandbox_plugin", "scratch-home"),
        sandbox_fallback_plugin=getattr(run_args, "sandbox_fallback_plugin", "passthrough"),
        governance_plugin=getattr(run_args, "governance_plugin", "strict-human-approval"),
        learn_accepted_feedback=False,
        learn_feedback_id=[],
        pr=getattr(run_args, "pr", ""),
        post_review_action=getattr(run_args, "post_review_action", "ask"),
    )


def handle_run_command(args: argparse.Namespace) -> int:
    target = _resolve_target_path(args.target)
    state_path = _resolve_target_path(args.state_file) if args.state_file else default_state_path(target=target)
    previous_state = load_state(state_path) if state_path.exists() else {}
    setup_tool_policy = resolve_setup_tool_policy(
        previous_state=previous_state,
        requested_mode=getattr(args, "tool_approval", None),
        reset_approvals=bool(getattr(args, "reset_tool_approvals", False)),
    )
    state_payload = dict(previous_state) if isinstance(previous_state, dict) else {}
    state_payload["schema_version"] = CURRENT_SCHEMA_VERSION
    state_payload["harness"] = args.harness
    state_payload["command_name"] = args.name
    state_payload["setup_tool_policy"] = setup_tool_policy
    save_state(state_path=state_path, payload=state_payload)
    if args.emit == "json":
        apply_bootstrap(target=target, name=args.name)
    else:
        run_bootstrap_with_status(target=target, harness=args.harness, name=args.name)
    review_args = _build_review_args_from_run_args(run_args=args, target=target)
    review_args.setup_tool_policy = setup_tool_policy
    return handle_review_command(review_args)


def _parse_selection_values(raw: object) -> list[str]:
    if isinstance(raw, str):
        tokens = [raw]
    elif isinstance(raw, list):
        tokens = [item for item in raw if isinstance(item, str)]
    else:
        return []
    values: list[str] = []
    for token in tokens:
        values.extend(parse_csv(token))
    return values


def _build_cli_inputs(args: argparse.Namespace) -> dict[str, list[str]]:
    return {
        "personas": _parse_selection_values(args.personas),
        "exclude_personas": _parse_selection_values(args.exclude_personas),
        "baselines": _parse_selection_values(args.baselines),
        "exclude_baselines": _parse_selection_values(args.exclude_baselines),
        "tools": _parse_selection_values(args.tools),
        "exclude_tools": _parse_selection_values(args.exclude_tools),
        "languages": _parse_selection_values(args.languages),
        "exclude_languages": _parse_selection_values(args.exclude_languages),
        "specialties": _parse_selection_values(args.specialties),
        "exclude_specialties": _parse_selection_values(args.exclude_specialties),
        "strategies": _parse_selection_values(args.strategies),
        "exclude_strategies": _parse_selection_values(args.exclude_strategies),
    }


def _preflight_review_scope(*, review_target: Path, base_ref: str) -> dict:
    candidate = base_ref.strip()
    if not candidate:
        return {"mode": "repo-current", "base_ref": None}

    rev_parse = subprocess.run(
        ["git", "-C", str(review_target), "rev-parse", "--verify", candidate],
        check=False,
        capture_output=True,
        text=True,
    )
    if rev_parse.returncode != 0:
        raise ValueError(f"Invalid base ref '{candidate}' for review scope preflight.")

    diff = subprocess.run(
        ["git", "-C", str(review_target), "diff", "--name-only", f"{candidate}...HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if diff.returncode != 0:
        raise ValueError(f"Unable to compute review diff for '{candidate}...HEAD'.")
    changed_files = [line.strip() for line in diff.stdout.splitlines() if line.strip()]
    if not changed_files:
        raise ValueError(f"Review scope '{candidate}...HEAD' has no changed files.")
    return {"mode": "git-diff", "base_ref": candidate, "changed_files": changed_files}


def _append_feedback_action(plan: dict, action: dict) -> None:
    if not isinstance(action, dict) or not action.get("id"):
        return
    existing = {
        item.get("id")
        for item in plan.get("feedback_actions", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if action["id"] in existing:
        return
    plan["feedback_actions"] = [*plan.get("feedback_actions", []), action]
    priority = action.get("priority", "P3")
    title = action.get("title", "Feedback item")
    directive = action.get("action", "")
    plan["feedback"] = [*plan.get("feedback", []), f"[{priority}] {title} — Action: {directive}"]


def _choose_uninstall_for_deselected_tools(*, has_deselected_tools: bool) -> bool:
    if not has_deselected_tools:
        return False
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return False
    answer = input("Deselected tools have uninstall commands. Run uninstall now? [y/N]: ").strip().lower()
    return answer in {"y", "yes"}


def _callable_accepts_keyword(callable_obj: object, keyword: str) -> bool:
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return True
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD or parameter.name == keyword:
            return True
    return False


def _invoke_with_optional_environment(method, *, command_environment: dict[str, str] | None = None, **kwargs):
    if command_environment is not None and _callable_accepts_keyword(method, "command_environment"):
        return method(command_environment=command_environment, **kwargs)
    return method(**kwargs)


def _detect_active_pull_request(
    *,
    review_target: Path,
    pr_ref: str | None = None,
    provider_preference: str = "auto",
) -> dict | None:
    registry = build_default_provider_registry()
    if provider_preference in registry.pr_providers:
        provider = registry.pr_providers[provider_preference]
    else:
        provider = registry.resolve_pr_provider(review_target=review_target, pr_ref=pr_ref)
    pull_request = provider.detect_pull_request(review_target=review_target, pr_ref=pr_ref)
    if pull_request is not None:
        return pull_request
    if provider.id != "github":
        fallback = registry.pr_providers.get("github")
        if fallback is not None:
            return fallback.detect_pull_request(review_target=review_target, pr_ref=pr_ref)
    return None


def _is_tooling_feedback_action(action: dict) -> bool:
    action_id = str(action.get("id", "")).strip()
    return action_id in {
        "deterministic-tool-gate-failed",
        "deterministic-review-gate-failed",
        "execution-plugin-fallback",
        "sandbox-plugin-fallback",
        "no-deterministic-gates",
    }


def _conventional_comment_prefix(action: dict) -> tuple[str, str]:
    priority = str(action.get("priority", "P3")).strip().upper()
    action_id = str(action.get("id", "")).strip()
    if action_id in {"ui-ux-persona-not-selected", "shared-checks-centralized", "fixed-routing-with-strategies"}:
        return "note", "(non-blocking)"
    if _is_tooling_feedback_action(action):
        if priority == "P1":
            return "issue", "(blocking)"
        return "chore", "(non-blocking)"
    if priority == "P1":
        return "issue", "(blocking)"
    if priority == "P2":
        return "suggestion", "(non-blocking)"
    return "note", "(non-blocking)"


def _render_conventional_feedback_item(action: dict) -> list[str]:
    label, decoration = _conventional_comment_prefix(action)
    title = str(action.get("title", "Feedback item")).strip()
    directive = str(action.get("action", "")).strip()
    reason = str(action.get("why", "")).strip()
    lines = [f"- **{label} {decoration}:** {title}"]
    if reason:
        lines.append(f"  {reason}")
    if directive:
        lines.append(f"  Next step: {directive}")
    return lines


def _render_block_summary(actions: list[dict]) -> list[str]:
    blocking_actions = [item for item in actions if str(item.get("priority", "")).upper() == "P1"]
    if blocking_actions:
        top_titles = ", ".join(str(item.get("title", "Issue")).strip() for item in blocking_actions[:2])
        return [f"**What failed:** {top_titles}."]
    if actions:
        return ["**What failed:** no blocking failures; advisory improvements are available."]
    return ["**What failed:** no actionable findings were recorded."]


def _render_urgency_summary(actions: list[dict]) -> str:
    blocking_count = sum(1 for item in actions if str(item.get("priority", "")).upper() == "P1")
    advisory_count = len(actions) - blocking_count
    if blocking_count:
        return f"**How urgent:** {blocking_count} blocking, {advisory_count} non-blocking."
    return f"**How urgent:** non-blocking only ({advisory_count})."


def _render_next_steps(actions: list[dict]) -> list[str]:
    steps: list[str] = []
    for item in actions:
        directive = str(item.get("action", "")).strip()
        if directive:
            steps.append(directive)
        if len(steps) >= 3:
            break
    if not steps:
        return ["1. No immediate action is required."]
    return [f"{index}. {step}" for index, step in enumerate(steps, start=1)]


def _render_context_details(plan: dict, pull_request: dict) -> list[str]:
    provider = str(pull_request.get("provider", "")).strip().lower()
    pr_number = str(pull_request.get("number", "")).strip()
    context_rows = []
    if provider == "ado":
        context_rows.append(f"- Pull request ID: `{pr_number}`")
    elif provider:
        context_rows.append(f"- PR: `#{pr_number}`")
    scope = str(plan.get("review_scope", {}).get("mode", "repo-current"))
    axes = ", ".join(plan.get("review_axes", ["standards", "spec"]))
    context_rows.append(f"- Scope: `{scope}`")
    context_rows.append(f"- Axes: `{axes}`")
    requirements = plan.get("requirements_compliance", {})
    if isinstance(requirements, dict):
        issue_provider = str(requirements.get("issue_provider", "")).strip().lower()
        issue_ref = str(requirements.get("issue_ref", "")).strip()
        if issue_provider == "ado" and issue_ref:
            work_item_id = extract_ado_work_item_id(issue_ref)
            if work_item_id:
                context_rows.append(f"- Work item: `{work_item_id}`")
    return ["<details>", "<summary>Context (optional)</summary>", "", *context_rows, "", "</details>"]


def _render_pr_comment_body(*, plan: dict, pull_request: dict) -> str:
    actions = [item for item in plan.get("feedback_actions", []) if isinstance(item, dict)]
    priority_weight = {"P1": 1, "P2": 2, "P3": 3}
    actions.sort(
        key=lambda item: (priority_weight.get(str(item.get("priority", "P3")), 99), str(item.get("title", "")))
    )
    tool_actions = [item for item in actions if _is_tooling_feedback_action(item)]
    finding_actions = [item for item in actions if not _is_tooling_feedback_action(item)]
    blocking_tool_actions = [item for item in tool_actions if str(item.get("priority", "")).upper() == "P1"]
    non_blocking_tool_actions = [item for item in tool_actions if str(item.get("priority", "")).upper() != "P1"]
    blocking_findings = [item for item in finding_actions if str(item.get("priority", "")).upper() == "P1"]
    non_blocking_findings = [item for item in finding_actions if str(item.get("priority", "")).upper() != "P1"]
    lines = [
        "## crk findings (Conventional Comments)",
        "",
        *_render_block_summary(actions),
        _render_urgency_summary(actions),
        "",
        "**What to do next:**",
        *_render_next_steps(actions),
        "",
        "Format: `<label> (decoration): subject` with supporting discussion and next step.",
        "",
    ]
    if tool_actions:
        lines.extend(["### Tooling status", ""])
        if blocking_tool_actions:
            lines.append("#### Blocking")
            for item in blocking_tool_actions[:5]:
                lines.extend(_render_conventional_feedback_item(item))
        if non_blocking_tool_actions:
            lines.append("#### Non-blocking")
            for item in non_blocking_tool_actions[:5]:
                lines.extend(_render_conventional_feedback_item(item))
        lines.append("")
    if finding_actions:
        lines.extend(["### Review findings", ""])
        if blocking_findings:
            lines.append("#### Blocking")
            for item in blocking_findings[:8]:
                lines.extend(_render_conventional_feedback_item(item))
        if non_blocking_findings:
            lines.append("#### Non-blocking")
            for item in non_blocking_findings[:8]:
                lines.extend(_render_conventional_feedback_item(item))
    elif not tool_actions:
        lines.append("- **praise:** No actionable findings were recorded.")
    lines.extend(["", *_render_context_details(plan, pull_request)])
    return "\n".join(lines) + "\n"


def _extract_line_comment_threads(plan: dict) -> list[dict]:
    findings = plan.get("findings", [])
    if not isinstance(findings, list):
        return []
    threads: list[dict] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        file_path = str(finding.get("file", "")).strip()
        line = finding.get("line")
        issue = str(finding.get("issue", "")).strip()
        recommendation = str(finding.get("recommendation", "")).strip()
        severity = str(finding.get("severity", "")).strip().lower()
        if not file_path or not isinstance(line, int) or line < 1:
            continue
        if not issue and not recommendation:
            continue
        normalized_path = file_path if file_path.startswith("/") else f"/{file_path}"
        label = "issue" if severity in {"blocking", "important"} else "suggestion"
        content_parts = [f"**{label}:** {issue or 'Action needed'}"]
        if recommendation:
            content_parts.append(f"Next step: {recommendation}")
        threads.append(
            {
                "file_path": normalized_path,
                "line": line,
                "content": "\n\n".join(content_parts),
            }
        )
    return threads[:20]


def _publish_pr_comment(*, review_target: Path, plan: dict) -> str | None:
    pr_ref = str(plan.get("review_pr_ref", "")).strip() or None
    provider_preference = str(plan.get("provider_preference", "auto"))
    pull_request = _detect_active_pull_request(
        review_target=review_target, pr_ref=pr_ref, provider_preference=provider_preference
    )
    if pull_request is None:
        return "No active PR found; skipping PR comment publication."

    body = _render_pr_comment_body(plan=plan, pull_request=pull_request)
    provider_id = str(pull_request.get("provider", "github"))
    registry = build_default_provider_registry()
    provider = registry.pr_providers.get(provider_id)
    if provider is None:
        raise ValueError(f"No PR provider is registered for '{provider_id}'.")
    line_comments = _extract_line_comment_threads(plan)
    publish_signature = inspect.signature(provider.publish_comment)
    if "line_comments" in publish_signature.parameters:
        return provider.publish_comment(
            review_target=review_target,
            pull_request=pull_request,
            body=body,
            line_comments=line_comments,
        )
    return provider.publish_comment(review_target=review_target, pull_request=pull_request, body=body)


def _resolve_post_review_action(*, requested: str, active_pr: dict | None) -> str:
    if requested != "ask":
        return requested
    if active_pr is None:
        return "plan"
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return "plan"
    answer = input("Post actionable comments to the active PR now? [Y/p/n]: ").strip().lower()
    if answer in {"", "y", "yes"}:
        return "comment"
    if answer in {"p", "plan"}:
        return "plan"
    return "skip"


_HARNESS_CHOICES = ["copilot", "claude-code", "opencode"]
_HARNESS_LABELS = {"copilot": "GitHub Copilot", "claude-code": "Claude Code", "opencode": "OpenCode"}


def _prompt_harness_selection() -> str:
    print("\nChoose the AI harness for persona execution:")
    for index, harness_id in enumerate(_HARNESS_CHOICES, 1):
        print(f"  {index}. {_HARNESS_LABELS[harness_id]} ({harness_id})")
    print("  Enter number or harness id (or press Enter to skip): ", end="", flush=True)
    try:
        raw = input().strip()
    except (EOFError, KeyboardInterrupt):
        return ""
    if not raw:
        return ""
    if raw.isdigit():
        index = int(raw) - 1
        if 0 <= index < len(_HARNESS_CHOICES):
            return _HARNESS_CHOICES[index]
        return ""
    return raw if raw in _HARNESS_CHOICES else ""


def _save_review_harness(*, review_target: Path, harness: str, plan: dict, previous_state: dict) -> None:
    state_path = default_state_path(target=review_target)
    if not state_path.exists():
        return
    merged = {**previous_state, "harness": harness}
    save_state(state_path=state_path, payload=merged)


def _apply_review_wizard(
    *,
    args: argparse.Namespace,
    config: dict,
    catalog: tuple[list[dict], list[dict], list[dict], list[dict], list[dict], list[dict]],
) -> tuple[dict, dict[str, list[str]], bool]:
    personas, baselines, tools, languages, specialties, strategies = catalog
    resolved_target = _resolve_target_path(args.target)
    selections = run_review_wizard(
        target=str(resolved_target),
        personas=personas,
        baselines=baselines,
        tools=tools,
        languages=languages,
        specialties=specialties,
        strategies=strategies,
        config=config,
    )
    if selections is None:
        return config, _build_cli_inputs(args), False
    return (
        {
            **config,
            "personas": selections["personas"],
            "baselines": selections["baselines"],
            "baseline_practices": selections.get("baseline_practices", []),
            "tools": selections["tools"],
            "languages": selections["languages"],
            "language_practices": selections.get("language_practices", []),
            "specialties": selections["specialties"],
            "strategies": selections["strategies"],
            "harness": selections.get("harness") or config.get("harness", ""),
        },
        _build_cli_inputs(args),
        True,
    )


def _run_review_effects(
    *,
    plan: dict,
    review_target: Path,
    run_setup: bool = True,
    setup_results: list[dict] | None = None,
    setup_error: str | None = None,
    show_progress: bool = False,
    execution_plugin_id: str = "shell-local",
    execution_fallback_plugin_id: str | None = None,
    sandbox_plugin_id: str = "scratch-home",
    sandbox_fallback_plugin_id: str | None = None,
    setup_approval_policy: dict | None = None,
) -> dict:
    if show_progress:
        selections = plan.get("selections", {}) if isinstance(plan.get("selections"), dict) else {}
        persona_count = len(selections.get("personas", [])) if isinstance(selections.get("personas"), list) else 0
        tool_count = len(selections.get("tools", [])) if isinstance(selections.get("tools"), list) else 0
        print(f"---- Starting crk workflow ({persona_count} personas, {tool_count} tools) ----")
        if run_setup:
            print("⠋ Running setup and verification checks...")
    execution_registry = build_default_execution_registry(
        run_selected_tool_setup=run_selected_tool_setup,
        run_deterministic_gates=run_deterministic_gates,
    )
    sandbox_registry = build_default_sandbox_registry()
    execution_plugin = execution_registry.resolve(execution_plugin_id)
    sandbox_plugin = sandbox_registry.resolve(sandbox_plugin_id)
    plan["execution_plugin"] = execution_plugin.id
    plan["sandbox_plugin"] = sandbox_plugin.id

    def _run_execution_pass(
        *, command_environment: dict[str, str] | None
    ) -> tuple[list[dict], str | None, list[dict], str | None]:
        if run_setup:
            run_setup_kwargs = {
                "deterministic_gates": plan.get("deterministic_gates", []),
                "interactive": show_progress,
            }
            if _callable_accepts_keyword(execution_plugin.run_setup, "approval_policy"):
                run_setup_kwargs["approval_policy"] = setup_approval_policy
            try:
                tool_setup_results, tool_setup_error = _invoke_with_optional_environment(
                    execution_plugin.run_setup,
                    command_environment=command_environment,
                    **run_setup_kwargs,
                )
            except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as primary_error:
                fallback_id = (execution_fallback_plugin_id or "").strip()
                if fallback_id and fallback_id != execution_plugin.id:
                    fallback_plugin = execution_registry.resolve(fallback_id)
                    plan["execution_plugin_fallback"] = fallback_plugin.id
                    fallback_setup_kwargs = {
                        "deterministic_gates": plan.get("deterministic_gates", []),
                        "interactive": show_progress,
                    }
                    if _callable_accepts_keyword(fallback_plugin.run_setup, "approval_policy"):
                        fallback_setup_kwargs["approval_policy"] = setup_approval_policy
                    tool_setup_results, tool_setup_error = _invoke_with_optional_environment(
                        fallback_plugin.run_setup,
                        command_environment=command_environment,
                        **fallback_setup_kwargs,
                    )
                    _append_feedback_action(
                        plan,
                        {
                            "id": "execution-plugin-fallback",
                            "priority": "P2",
                            "title": "Execution plugin fallback used",
                            "action": f"Investigate primary execution plugin '{execution_plugin.id}' failure.",
                            "why": str(primary_error),
                        },
                    )
                else:
                    raise
        else:
            tool_setup_results = (
                setup_results if setup_results is not None else list(plan.get("tool_setup_results", []))
            )
            tool_setup_error = setup_error if setup_error is not None else plan.get("tool_setup_error")
        if show_progress:
            if tool_setup_error is None:
                print("✔ Setup and verification checks complete.")
            else:
                print(f"✖ Setup and verification checks failed: {tool_setup_error}")
            print("---- Running deterministic review gates ----")
        plan["tool_setup_results"] = tool_setup_results
        plan["tool_setup_error"] = tool_setup_error
        tool_feedback_actions, tool_feedback = tool_setup_feedback(tool_setup_results, tool_setup_error)
        if tool_feedback_actions:
            plan["feedback_actions"] = [*plan.get("feedback_actions", []), *tool_feedback_actions]
            plan["feedback"] = [*plan.get("feedback", []), *tool_feedback]

        try:
            tool_review_results, tool_review_error = _invoke_with_optional_environment(
                execution_plugin.run_review,
                command_environment=command_environment,
                target=review_target,
                deterministic_gates=plan.get("deterministic_gates", []),
                interactive=show_progress,
            )
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as primary_error:
            fallback_id = (execution_fallback_plugin_id or "").strip()
            if fallback_id and fallback_id != execution_plugin.id:
                fallback_plugin = execution_registry.resolve(fallback_id)
                plan["execution_plugin_fallback"] = fallback_plugin.id
                tool_review_results, tool_review_error = _invoke_with_optional_environment(
                    fallback_plugin.run_review,
                    command_environment=command_environment,
                    target=review_target,
                    deterministic_gates=plan.get("deterministic_gates", []),
                    interactive=show_progress,
                )
                _append_feedback_action(
                    plan,
                    {
                        "id": "execution-plugin-fallback",
                        "priority": "P2",
                        "title": "Execution plugin fallback used",
                        "action": f"Investigate primary execution plugin '{execution_plugin.id}' failure.",
                        "why": str(primary_error),
                    },
                )
            else:
                raise
        if show_progress:
            if tool_review_error is None:
                print("✔ Deterministic review gates complete.")
            else:
                print(f"✖ Deterministic review gates failed: {tool_review_error}")
        return tool_setup_results, tool_setup_error, tool_review_results, tool_review_error

    sandbox_error: Exception | None = None
    try:
        with sandbox_plugin.enter(target=review_target) as sandbox_session:
            plan["sandbox"] = {
                "plugin": sandbox_session.id,
                "note": sandbox_session.note,
            }
            tool_setup_results, tool_setup_error, tool_review_results, tool_review_error = _run_execution_pass(
                command_environment=sandbox_session.environment,
            )
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as primary_error:
        sandbox_error = primary_error
        fallback_id = (sandbox_fallback_plugin_id or "").strip()
        if fallback_id and fallback_id != sandbox_plugin.id:
            fallback_plugin = sandbox_registry.resolve(fallback_id)
            plan["sandbox_fallback"] = fallback_plugin.id
            with fallback_plugin.enter(target=review_target) as sandbox_session:
                plan["sandbox"] = {
                    "plugin": sandbox_session.id,
                    "note": sandbox_session.note,
                }
                tool_setup_results, tool_setup_error, tool_review_results, tool_review_error = _run_execution_pass(
                    command_environment=sandbox_session.environment,
                )
            _append_feedback_action(
                plan,
                {
                    "id": "sandbox-plugin-fallback",
                    "priority": "P2",
                    "title": "Sandbox plugin fallback used",
                    "action": f"Investigate primary sandbox plugin '{sandbox_plugin.id}' failure.",
                    "why": str(primary_error),
                },
            )
        else:
            raise

    plan["sandbox_error"] = str(sandbox_error) if sandbox_error is not None else None
    plan["tool_evidence"] = build_tool_evidence(setup_results=tool_setup_results, review_results=tool_review_results)
    attach_tool_evidence_to_units(units=plan.get("units", []), tool_evidence=plan["tool_evidence"])
    for unit in plan.get("units", []):
        if isinstance(unit, dict):
            unit["prompt_context"] = build_unit_prompt_context(unit=unit)

    plan["tool_setup_results"] = tool_setup_results
    plan["tool_setup_error"] = tool_setup_error
    plan["tool_review_results"] = tool_review_results
    plan["tool_review_error"] = tool_review_error
    review_feedback_actions, review_feedback = tool_review_feedback(tool_review_results, tool_review_error)
    if review_feedback_actions:
        plan["feedback_actions"] = [*plan.get("feedback_actions", []), *review_feedback_actions]
        plan["feedback"] = [*plan.get("feedback", []), *review_feedback]

    if tool_setup_error is None and tool_review_error is None:
        plan["feedback_actions"] = [
            item
            for item in plan.get("feedback_actions", [])
            if isinstance(item, dict) and item.get("id") != "feedback-loop-next-step"
        ]
        plan["feedback"] = [
            line for line in plan.get("feedback", []) if "Feedback loop next step is ready" not in str(line)
        ]
    return plan


def _failed_gate_entries(plan: dict) -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    for phase, key in (("setup", "tool_setup_results"), ("review", "tool_review_results")):
        results = plan.get(key, [])
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, dict) or result.get("status") != "failed":
                continue
            tool_id = str(result.get("id", "unknown-tool"))
            failed_step = next(
                (
                    step
                    for step in result.get("steps", [])
                    if isinstance(step, dict) and step.get("status") == "failed" and step.get("text")
                ),
                None,
            )
            command = (
                str(failed_step.get("text", "unknown command")) if isinstance(failed_step, dict) else "unknown command"
            )
            entries.append((phase, tool_id, command))
    return entries


def _render_gate_failure_summary(plan: dict) -> str:
    entries = _failed_gate_entries(plan)
    if not entries:
        return ""
    lines = [
        "---- Gate failures ----",
        "",
    ]
    for phase, tool_id, command in entries:
        lines.append(f"✖ {tool_id} ({phase})")
        lines.append(f"  command: {command}")
    lines.append("")
    lines.append("Review completed with partial gate coverage. Fix failing commands and rerun impacted units.")
    return "\n".join(lines) + "\n"


def _render_completion_summary(plan: dict, *, post_action: str) -> str:
    feedback_actions = [item for item in plan.get("feedback_actions", []) if isinstance(item, dict)]
    units_total = len(plan.get("units", [])) if isinstance(plan.get("units"), list) else 0
    blocking = sum(1 for item in feedback_actions if str(item.get("priority")) == "P1")
    gate_failures = len(_failed_gate_entries(plan))
    report_path = (
        plan.get("feedback_report", {}).get("path", "") if isinstance(plan.get("feedback_report"), dict) else ""
    )
    state_path = plan.get("feedback_state", {}).get("path", "") if isinstance(plan.get("feedback_state"), dict) else ""
    queue_path = (
        plan.get("feedback_learning_queue", {}).get("path", "")
        if isinstance(plan.get("feedback_learning_queue"), dict)
        else ""
    )
    promotion = plan.get("learning_promotion", {}) if isinstance(plan.get("learning_promotion"), dict) else {}
    governance = plan.get("governance", {}) if isinstance(plan.get("governance"), dict) else {}
    persona_runs = plan.get("persona_runs", [])
    harness = plan.get("harness") or ""
    if isinstance(persona_runs, list) and persona_runs:
        ran = sum(1 for item in persona_runs if isinstance(item, dict) and item.get("status") == "ran")
        skipped = sum(1 for item in persona_runs if isinstance(item, dict) and item.get("status") == "skipped")
        failed = sum(1 for item in persona_runs if isinstance(item, dict) and item.get("status") == "failed")
        persona_line = f"Persona execution: ran {ran} · skipped {skipped} · failed {failed}"
    else:
        if harness:
            persona_line = f"Persona execution: plan ready · open in {harness} to run AI persona reviews"
        else:
            persona_line = (
                "Persona execution: plan ready · set --harness (copilot|claude-code|opencode) to run AI reviews"
            )
    lines = [
        "---- Complete ----",
        "",
        f"✔ Review finished · units: {units_total} · findings: {len(feedback_actions)} · blocking: {blocking}",
        persona_line,
    ]
    if gate_failures:
        lines.append(f"✖ Deterministic gate failures: {gate_failures}")
    if post_action == "comment":
        lines.append("PR comment action: attempted")
    elif post_action == "plan":
        lines.append("PR comment action: skipped (plan mode)")
    else:
        lines.append("PR comment action: skipped")
    if report_path:
        lines.append(f"Feedback report: {report_path}")
    if state_path:
        lines.append(f"Feedback state: {state_path}")
    if queue_path:
        lines.append(f"Learning queue: {queue_path}")
    if plan.get("execution_plugin"):
        lines.append(f"Execution plugin: {plan.get('execution_plugin')}")
    if plan.get("execution_plugin_fallback"):
        lines.append(f"Execution fallback: {plan.get('execution_plugin_fallback')}")
    if plan.get("sandbox_plugin"):
        lines.append(f"Sandbox plugin: {plan.get('sandbox_plugin')}")
    if plan.get("sandbox_fallback"):
        lines.append(f"Sandbox fallback: {plan.get('sandbox_fallback')}")
    if governance:
        lines.append(f"Governance plugin: {governance.get('plugin', '')}")
        lines.append(f"Governance approved: {'yes' if governance.get('approved') else 'no'}")
    promoted_ids = promotion.get("promoted_ids", [])
    if isinstance(promoted_ids, list):
        lines.append(f"Accepted feedback promoted: {len(promoted_ids)}")
    lines.append('Promote accepted items with: --learn-best-practice "<practice text>"')
    lines.append('Update status with: --feedback-status "<feedback_id>:<status>"')
    return "\n".join(lines) + "\n"


def _render_compact_review_output(plan: dict) -> str:
    selections = plan.get("selections", {}) if isinstance(plan.get("selections"), dict) else {}
    feedback_actions = [item for item in plan.get("feedback_actions", []) if isinstance(item, dict)]
    lines = [
        "crk summary",
        "===================",
        "",
        f"Target: {plan.get('target', '.')}",
        f"Workflow: {plan.get('review_workflow', 'dev-loop')}",
        f"Personas: {', '.join(selections.get('personas', [])) or 'none'}",
        f"Tools: {', '.join(selections.get('tools', [])) or 'none'}",
        f"Languages: {', '.join(selections.get('languages', [])) or 'none'}",
        f"Findings: {len(feedback_actions)}",
        f"Blocking: {sum(1 for item in feedback_actions if str(item.get('priority')) == 'P1')}",
        f"Schema: {plan.get('schema_version', 'n/a')}",
        f"Sandbox: {plan.get('sandbox_plugin', 'scratch-home')}",
        "",
    ]
    migration_notes = plan.get("migration_notes", [])
    if isinstance(migration_notes, list) and migration_notes:
        lines.append(f"Migration notes: {len(migration_notes)}")
        lines.append("")
    if feedback_actions:
        lines.append("Top actions")
        lines.append("-----------")
        lines.append("")
        for item in feedback_actions[:5]:
            lines.append(f"- [{item.get('priority', 'P3')}] {item.get('title', 'Feedback item')}")
            action = str(item.get("action", "")).strip()
            if action:
                lines.append(f"  - Action: {action}")
        lines.append("")
    lines.append("Use -vvv for full plan details.")
    return "\n".join(lines) + "\n"


def _apply_requirements_walkthrough(*, args: argparse.Namespace, review_target: Path, plan: dict) -> dict:
    preferred_issue_provider = args.issue_provider
    provider_preference = str(getattr(args, "provider", "auto"))
    if preferred_issue_provider == "auto" and provider_preference in {"github", "ado", "jira"}:
        preferred_issue_provider = provider_preference
    requirements_context = derive_requirements(
        target=review_target,
        issue_ref=args.requirements_issue,
        issue_provider=preferred_issue_provider,
        user_requirements=parse_csv(args.requirements),
    )
    if (
        args.requirements_walkthrough
        and args.emit == "markdown"
        and sys.stdin.isatty()
        and sys.stdout.isatty()
        and requirements_context.get("requirements")
    ):
        print("\n## Requirements walkthrough\n")
        for item in requirements_context["requirements"]:
            print(f"[{item['id']}] ({item['source']}/{item['confidence']}) {item['text']}")
        if getattr(args, "requirements_refiner", "manual") == "grilling":
            print("\n## Requirements grill session\n")
            requirements_context = apply_grilling_refinement(requirements=requirements_context)
            requirements_context["walkthrough_confirmed"] = True
            plan["requirements_compliance"] = requirements_context
            return plan
        keep_prompt = input("\nUse these derived requirements? [Y/n]: ").strip().lower()
        if keep_prompt in {"n", "no"}:
            remove_raw = input("IDs to remove (comma-separated, blank for none): ").strip()
            removed_ids = {int(part.strip()) for part in remove_raw.split(",") if part.strip().isdigit()}
            added_items: list[str] = []
            while True:
                addition = input("Add requirement (blank to finish): ").strip()
                if not addition:
                    break
                added_items.append(addition)
            requirements_context = apply_walkthrough_overrides(
                requirements=requirements_context,
                removed_ids=removed_ids,
                added_items=added_items,
            )
        else:
            requirements_context["walkthrough_confirmed"] = True
    plan["requirements_compliance"] = requirements_context
    return plan


def _attach_default_requirements_context(*, args: argparse.Namespace, review_target: Path, plan: dict) -> dict:
    preferred_issue_provider = args.issue_provider
    provider_preference = str(getattr(args, "provider", "auto"))
    if preferred_issue_provider == "auto" and provider_preference in {"github", "ado", "jira"}:
        preferred_issue_provider = provider_preference
    if args.requirements_check:
        return _apply_requirements_walkthrough(args=args, review_target=review_target, plan=plan)
    requirements_context = derive_requirements(
        target=review_target,
        issue_ref=args.requirements_issue,
        issue_provider=preferred_issue_provider,
        user_requirements=parse_csv(args.requirements),
    )
    plan["requirements_compliance"] = requirements_context
    if not requirements_context.get("requirements"):
        _append_feedback_action(
            plan,
            {
                "id": "spec-axis-empty",
                "priority": "P2",
                "title": "Spec axis has no requirements context",
                "action": "Run review with --requirements-check or provide --requirements/--requirements-issue so the spec axis can validate intent.",
                "why": "The review can only compare against explicit or derived requirements when some requirements context exists.",
            },
        )
    return plan


def _finalize_review_output(*, plan: dict, review_target: Path, args: argparse.Namespace) -> int:
    if not isinstance(plan.get("findings"), list):
        plan["findings"] = []
    plan["schema_version"] = CURRENT_SCHEMA_VERSION
    feedback_report_path = write_feedback_report(target=review_target, plan=plan)
    feedback_state_path = update_feedback_state(target=review_target, plan=plan)
    if args.feedback_status:
        feedback_state_path = apply_feedback_status_updates(
            target=review_target,
            updates=[value for value in args.feedback_status if isinstance(value, str) and value.strip()],
        )
    feedback_learning_queue_path = write_feedback_learning_queue(target=review_target)
    learning_promotion: dict | None = None
    if bool(getattr(args, "learn_accepted_feedback", False)):
        learning_promotion = promote_accepted_feedback_to_learnings(
            target=review_target,
            feedback_ids=[value for value in getattr(args, "learn_feedback_id", []) if isinstance(value, str)],
        )
        feedback_learning_queue_path = default_feedback_learning_queue_path(target=review_target)
    plan["feedback_report"] = {"path": str(feedback_report_path)}
    plan["feedback_state"] = {"path": str(feedback_state_path)}
    plan["feedback_learning_queue"] = {"path": str(feedback_learning_queue_path)}
    if learning_promotion is not None:
        plan["learning_promotion"] = learning_promotion
    pr_ref = str(plan.get("review_pr_ref", "")).strip() or None
    provider_preference = str(plan.get("provider_preference", "auto"))
    active_pr = _detect_active_pull_request(
        review_target=review_target, pr_ref=pr_ref, provider_preference=provider_preference
    )
    post_action = _resolve_post_review_action(
        requested=getattr(args, "post_review_action", "ask"),
        active_pr=active_pr,
    )
    governance_registry = build_default_governance_registry()
    governance_plugin = governance_registry.resolve(getattr(args, "governance_plugin", "strict-human-approval"))
    governance_decision = governance_plugin.decide_pr_publish(
        requested_action=post_action,
        active_pr=active_pr,
        interactive=bool(sys.stdin.isatty() and sys.stdout.isatty()),
    )
    plan["governance"] = {
        "plugin": governance_plugin.id,
        "approved": governance_decision.approved,
        "reason": governance_decision.reason,
        "evidence": governance_decision.evidence,
    }
    if post_action == "comment" and not governance_decision.approved:
        post_action = "skip"
    if args.emit == "json":
        print(json.dumps(plan, indent=2))
        return 0
    verbose_level = int(getattr(args, "verbose", 0) or 0)
    if verbose_level >= 3:
        print(to_markdown(plan))
    else:
        print(_render_compact_review_output(plan))
    gate_failure_summary = _render_gate_failure_summary(plan)
    if gate_failure_summary:
        print(gate_failure_summary)
    if learning_promotion is not None:
        promoted_count = len(learning_promotion.get("promoted_ids", []))
        print(f"Promoted accepted feedback items into repo learnings: {promoted_count}")
    if post_action == "comment":
        message = _publish_pr_comment(review_target=review_target, plan=plan)
        if message:
            print(message)
    elif post_action == "plan":
        print("Implementation plan is captured in the review output above.")
    elif str(governance_decision.reason).strip():
        print(governance_decision.reason)
    print(_render_completion_summary(plan, post_action=post_action))
    return 0


def handle_review_command(args: argparse.Namespace) -> int:
    review_target = _resolve_target_path(args.target)
    scope_preflight = _preflight_review_scope(review_target=review_target, base_ref=args.base_ref)
    config_raw = load_json(args.config) if args.config else {}
    config, migration_notes = migrate_config_payload(config_raw)
    if args.learn_best_practice:
        record_learned_practices(
            target=review_target, practices=[item for item in args.learn_best_practice if isinstance(item, str)]
        )
    config = merge_learned_extensions(config=config, target=review_target)

    # Load previous state and inject harness + prior selections into config for wizard pre-population
    previous_state = load_state(default_state_path(target=review_target))
    if previous_state:
        state_config = state_to_wizard_config(previous_state)
        # Merge state selections into config only for keys not already set by config file
        for key, value in state_config.items():
            if key not in config:
                config[key] = value

    # Resolve harness: CLI flag > state.json > interactive prompt
    harness: str = getattr(args, "harness", None) or ""
    if not harness:
        harness = previous_state.get("harness", "") or ""
    if not harness and not args.wizard and sys.stdin.isatty() and sys.stdout.isatty():
        harness = _prompt_harness_selection()
    if harness:
        config["harness"] = harness

    personas, baselines, tools, languages, specialties, strategies = build_dynamic_catalog(config)
    if args.list_catalog:
        print(to_catalog_markdown(personas, baselines, tools, languages, specialties, strategies))
        return 0

    strategy_mode = config.get("strategy_mode", args.strategy_mode)
    if strategy_mode not in {"overlay", "fanout"}:
        msg = "strategy_mode must be 'overlay' or 'fanout'."
        raise ValueError(msg)

    token_policy = resolve_token_policy(config, args)
    cli_inputs = _build_cli_inputs(args)
    if args.wizard:
        config, cli_inputs, accepted = _apply_review_wizard(
            args=args,
            config=config,
            catalog=(personas, baselines, tools, languages, specialties, strategies),
        )
        if not accepted:
            return 130
        # Re-resolve harness from wizard result
        harness = str(config.get("harness", "") or "")

    plan = build_plan(
        target=str(review_target),
        personas=personas,
        baselines=baselines,
        tools=tools,
        languages=languages,
        specialties=specialties,
        strategies=strategies,
        config=config,
        cli_inputs=cli_inputs,
        token_policy=token_policy,
        strategy_mode=strategy_mode,
    )
    plan["schema_version"] = CURRENT_SCHEMA_VERSION
    plan["migration_notes"] = migration_notes
    plan["provider_preference"] = getattr(args, "provider", "auto")
    plan["review_scope"] = scope_preflight
    plan["review_axes"] = ["standards", "spec"]
    plan["review_workflow"] = "pr-review" if str(getattr(args, "pr", "")).strip() else "dev-loop"
    if str(getattr(args, "pr", "")).strip():
        plan["review_pr_ref"] = str(args.pr).strip()
    if harness:
        plan["harness"] = harness
    plan = _run_review_effects(
        plan=plan,
        review_target=review_target,
        show_progress=args.emit == "markdown" and sys.stdin.isatty() and sys.stdout.isatty(),
        execution_plugin_id=getattr(args, "execution_plugin", "shell-local"),
        execution_fallback_plugin_id=getattr(args, "execution_fallback_plugin", ""),
        sandbox_plugin_id=getattr(args, "sandbox_plugin", "scratch-home"),
        sandbox_fallback_plugin_id=getattr(args, "sandbox_fallback_plugin", "passthrough"),
        setup_approval_policy=getattr(args, "setup_tool_policy", None),
    )
    # Persist harness to state so future review runs remember it
    if harness:
        _save_review_harness(review_target=review_target, harness=harness, plan=plan, previous_state=previous_state)
    plan = _attach_default_requirements_context(args=args, review_target=review_target, plan=plan)
    return _finalize_review_output(plan=plan, review_target=review_target, args=args)


def main() -> int:
    args = parse_args()
    if args.command == "uninstall":
        return handle_uninstall_command(args)
    if args.command == "run":
        return handle_run_command(args)
    if args.command in {"install", "init"}:
        return handle_install_or_init_command(args)
    return handle_review_command(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
