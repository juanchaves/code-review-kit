from __future__ import annotations

import inspect
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from textwrap import dedent

from .doc_contract import (
    harness_parity_lines,
    prompt_contract_lines,
    workflow_contract_lines,
    workflow_instructions_lines,
)
from .learning import record_learned_practices
from .migration import CURRENT_SCHEMA_VERSION, migrate_state_payload

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
CHECKMARK = "✅"


@dataclass(frozen=True)
class HarnessProfile:
    name: str
    summary: str
    command_name: str
    notes: list[str]


HARNESS_PROFILES: dict[str, HarnessProfile] = {
    "copilot": HarnessProfile(
        name="Copilot",
        summary="GitHub Copilot CLI / Copilot skill installation.",
        command_name="crk",
        notes=[
            "Install the repo bootstrap files so Copilot picks up the local review instructions and agent registration.",
            "If the command collides with an existing skill, pass --name to rename it.",
            "Use init to bootstrap the repo and launch the wizard in one step.",
        ],
    ),
    "claude-code": HarnessProfile(
        name="Claude Code",
        summary="Claude Code skill installation.",
        command_name="crk",
        notes=[
            "Install the skill as a slash command or equivalent harness command.",
            "If the command collides, rename it with --name.",
            "Keep the skill invocation consistent across harnesses; only the init wrapper changes.",
        ],
    ),
    "opencode": HarnessProfile(
        name="OpenCode",
        summary="OpenCode skill installation.",
        command_name="crk",
        notes=[
            "Install the skill as a first-class command in the harness.",
            "Use the same name unless the harness already reserves it.",
            "Fallback to a renamed command when collision occurs.",
        ],
    ),
}


@dataclass(frozen=True)
class BootstrapArtifact:
    path: Path
    content: str


@dataclass(frozen=True)
class BootstrapResult:
    target: Path
    created: list[Path]
    updated: list[Path]
    skipped: list[Path]


@dataclass(frozen=True)
class UninstallResult:
    target: Path
    removed: list[Path]
    skipped: list[Path]


SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
FEEDBACK_REPORT_DIR = "feedback"
FEEDBACK_REPORT_FILE = "latest.json"
FEEDBACK_STATE_FILE = "state.json"
FEEDBACK_LEARNING_QUEUE_FILE = "learning-queue.json"
FEEDBACK_STATUSES = {"open", "in_progress", "accepted", "dismissed", "done"}
PRIORITY_WEIGHT = {"P1": 1, "P2": 2, "P3": 3}
TOOL_APPROVAL_MODES = {"prompt", "allow-selected", "auto"}


def _supports_color() -> bool:
    return sys.stdout.isatty() and sys.stdin.isatty()


def _color(text: str, style: str) -> str:
    if not _supports_color():
        return text
    return f"{style}{text}{RESET}"


def _is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    if sys.platform != "linux":
        return False
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def platform_label() -> str:
    if sys.platform == "darwin":
        return "macOS"
    if sys.platform == "win32":
        return "Windows"
    return "Linux/WSL"


def _command_for_platform(note: str, platform_name: str) -> str | None:
    raw = note.strip()
    if not raw:
        return None
    if raw == "uv installed":
        return None
    if "macOS:" in raw or "Linux/WSL:" in raw:
        for part in raw.split(";"):
            item = part.strip()
            prefix = f"{platform_name}:"
            if item.startswith(prefix):
                return item[len(prefix) :].strip()
        return None
    return raw


def _run_shell_command(
    command: str,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    shell_command = command
    if "**" in shell_command:
        shell_command = f"shopt -s globstar nullglob; {shell_command}"
    return subprocess.run(
        ["bash", "-lc", shell_command],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd is not None else None,
        env={**os.environ, **env} if env is not None else None,
    )


def _run_shell_command_with_spinner(
    command: str,
    *,
    cwd: Path | None = None,
    phase: str = "setup",
    tool_id: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    shell_command = command
    if "**" in shell_command:
        shell_command = f"shopt -s globstar nullglob; {shell_command}"
    process = subprocess.Popen(
        ["bash", "-lc", shell_command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(cwd) if cwd is not None else None,
        env={**os.environ, **env} if env is not None else None,
    )
    command_label = command if len(command) <= 72 else f"{command[:69]}..."
    phase_label = f"{phase}:{tool_id}" if tool_id else phase
    frame_index = 0
    while process.poll() is None:
        frame = SPINNER_FRAMES[frame_index % len(SPINNER_FRAMES)]
        sys.stdout.write(f"\r{frame} {phase_label}: {command_label}")
        sys.stdout.flush()
        time.sleep(0.08)
        frame_index += 1
    stdout, stderr = process.communicate()
    success = process.returncode == 0
    status_icon = _color(CHECKMARK, GREEN) if success else _color("✖", RED)
    status_label = _color("passed", GREEN) if success else _color("failed", RED)
    sys.stdout.write(f"\r{status_icon} {phase_label}: {command_label} ({status_label}){' ' * 12}\n")
    sys.stdout.flush()
    return subprocess.CompletedProcess(
        args=["bash", "-lc", shell_command],
        returncode=process.returncode,
        stdout=stdout or "",
        stderr=stderr or "",
    )


def _callable_accepts_keyword(callable_obj: object, keyword: str) -> bool:
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return True
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD or parameter.name == keyword:
            return True
    return False


def _run_shell_command_with_environment(
    *,
    command: str,
    cwd: Path | None = None,
    phase: str | None = None,
    tool_id: str | None = None,
    interactive: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    runner = _run_shell_command_with_spinner if interactive else _run_shell_command
    kwargs: dict[str, object] = {}
    if cwd is not None and _callable_accepts_keyword(runner, "cwd"):
        kwargs["cwd"] = cwd
    if interactive:
        if _callable_accepts_keyword(runner, "phase"):
            kwargs["phase"] = phase or "setup"
        if _callable_accepts_keyword(runner, "tool_id"):
            kwargs["tool_id"] = tool_id
    if env is not None and _callable_accepts_keyword(runner, "env"):
        kwargs["env"] = env
    return runner(command, **kwargs)  # type: ignore[arg-type]


def _execute_setup_command(
    *,
    command: str,
    interactive: bool,
    phase: str,
    tool_id: str,
    command_environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if interactive and sys.stdin.isatty() and sys.stdout.isatty():
        return _run_shell_command_with_environment(
            command=command,
            phase=phase,
            tool_id=tool_id,
            interactive=True,
            env=command_environment,
        )
    return _run_shell_command_with_environment(command=command, interactive=False, env=command_environment)


def _build_active_context(items: dict) -> list[dict]:
    payload_context: list[dict] = []
    ranked = [
        item
        for item in items.values()
        if isinstance(item, dict) and item.get("active") and item.get("status") in {"open", "in_progress"}
    ]
    ranked.sort(
        key=lambda item: (
            PRIORITY_WEIGHT.get(str(item.get("priority", "P3")), 99),
            str(item.get("last_seen", "")),
        ),
    )
    for item in ranked[:8]:
        payload_context.append(
            {
                "id": item.get("id", ""),
                "priority": item.get("priority", "P3"),
                "status": item.get("status", "open"),
                "action": item.get("action", ""),
            }
        )
    return payload_context


def colorize_console_block(text: str) -> str:
    if not _supports_color():
        return text
    colored: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if line in {"crk setup", "crk bootstrap", "crk review summary", "crk uninstall"}:
            colored.append(_color(line, f"{BOLD}{CYAN}"))
            continue
        if set(line) == {"="} or line in {"Files", "Next step", "Feedback"}:
            colored.append(_color(line, f"{BOLD}{BLUE}"))
            continue
        if set(line) == {"-"}:
            colored.append(_color(line, BLUE))
            continue
        if line.startswith("- created:"):
            colored.append(_color(line, GREEN))
            continue
        if line.startswith("- updated:"):
            colored.append(_color(line, YELLOW))
            continue
        if line.startswith("- unchanged:"):
            colored.append(_color(line, DIM))
            continue
        if "[P1]" in line:
            colored.append(_color(line, RED))
            continue
        if "[P2]" in line:
            colored.append(_color(line, YELLOW))
            continue
        if "[P3]" in line:
            colored.append(_color(line, MAGENTA))
            continue
        colored.append(line)
    return "\n".join(colored) + ("\n" if text.endswith("\n") else "")


def _render_copilot_instructions(name: str) -> str:
    return dedent(
        f"""\
        # {name} bootstrap

        @.github/instructions/{name}.instructions.md
        """
    )


def _render_agent_file(name: str) -> str:
    lines = [
        "---",
        "description: Multi-persona code review with selectable baseline packs, tool packs, language packs, specialty packs, and challenge strategies.",
        "---",
        "",
        f"# {name}",
        "",
        "Run a low-token, high-signal review workflow.",
        "",
        *workflow_contract_lines(name),
        "",
        *harness_parity_lines(),
    ]
    return "\n".join(lines) + "\n"


def _render_code_review_instructions(name: str) -> str:
    lines = ["---", 'applyTo: "**/*"', "---", "", *workflow_instructions_lines(name)]
    return "\n".join(lines) + "\n"


def _render_prompt_file(name: str) -> str:
    lines = ["---", f"agent: {name}", "---", "", *prompt_contract_lines(name)]
    return "\n".join(lines) + "\n"


def build_bootstrap_artifacts(*, target: Path, name: str) -> list[BootstrapArtifact]:
    github_dir = target / ".github"
    instructions_dir = github_dir / "instructions"
    agents_dir = github_dir / "agents"
    prompts_dir = github_dir / "prompts"
    artifacts = [
        BootstrapArtifact(
            path=agents_dir / f"{name}.agent.md",
            content=_render_agent_file(name),
        ),
        BootstrapArtifact(
            path=instructions_dir / f"{name}.instructions.md",
            content=_render_code_review_instructions(name),
        ),
        BootstrapArtifact(
            path=prompts_dir / f"{name}.prompt.md",
            content=_render_prompt_file(name),
        ),
    ]

    copilot_instructions = github_dir / "copilot-instructions.md"
    if not copilot_instructions.exists():
        artifacts.append(
            BootstrapArtifact(
                path=copilot_instructions,
                content=_render_copilot_instructions(name),
            )
        )

    return artifacts


def apply_bootstrap(*, target: Path, name: str) -> BootstrapResult:
    created: list[Path] = []
    updated: list[Path] = []
    skipped: list[Path] = []

    for artifact in build_bootstrap_artifacts(target=target, name=name):
        artifact.path.parent.mkdir(parents=True, exist_ok=True)
        if artifact.path.exists():
            current = artifact.path.read_text(encoding="utf-8")
            if current == artifact.content:
                skipped.append(artifact.path.relative_to(target))
                continue
            updated.append(artifact.path.relative_to(target))
        else:
            created.append(artifact.path.relative_to(target))
        artifact.path.write_text(artifact.content, encoding="utf-8")

    return BootstrapResult(target=target, created=created, updated=updated, skipped=skipped)


def default_state_path(*, target: Path) -> Path:
    return target / ".code-review" / "state.json"


def default_feedback_report_path(*, target: Path) -> Path:
    return target / ".code-review" / FEEDBACK_REPORT_DIR / FEEDBACK_REPORT_FILE


def default_feedback_state_path(*, target: Path) -> Path:
    return target / ".code-review" / FEEDBACK_REPORT_DIR / FEEDBACK_STATE_FILE


def default_feedback_learning_queue_path(*, target: Path) -> Path:
    return target / ".code-review" / FEEDBACK_REPORT_DIR / FEEDBACK_LEARNING_QUEUE_FILE


def load_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {}
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("State file must contain a JSON object.")
    migrated, _notes = migrate_state_payload(payload)
    return migrated


def resolve_setup_tool_policy(
    *,
    previous_state: dict,
    requested_mode: str | None,
    reset_approvals: bool = False,
) -> dict:
    default_policy = {"mode": "allow-selected", "approved_commands": []}
    raw = previous_state.get("setup_tool_policy", {}) if isinstance(previous_state, dict) else {}
    policy = dict(default_policy)
    if isinstance(raw, dict):
        mode = raw.get("mode")
        approved = raw.get("approved_commands", [])
        if mode == "auto":
            mode = "allow-selected"
        if isinstance(mode, str) and mode in TOOL_APPROVAL_MODES:
            policy["mode"] = mode
        if isinstance(approved, list) and all(isinstance(item, str) for item in approved):
            policy["approved_commands"] = list(dict.fromkeys(item.strip() for item in approved if item.strip()))

    if requested_mode == "auto":
        requested_mode = "allow-selected"

    if requested_mode is not None:
        if requested_mode not in TOOL_APPROVAL_MODES:
            allowed = ", ".join(sorted(TOOL_APPROVAL_MODES))
            raise ValueError(f"Invalid tool approval mode '{requested_mode}'. Allowed: {allowed}.")
        policy["mode"] = requested_mode

    if reset_approvals:
        policy["approved_commands"] = []

    return policy


def save_state(*, state_path: Path, payload: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_feedback_report(*, target: Path, plan: dict) -> Path:
    report_path = default_feedback_report_path(target=target)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    persona_runs = plan.get("persona_runs", [])
    if not isinstance(persona_runs, list):
        persona_runs = []
    if not persona_runs:
        units = plan.get("units", [])
        if isinstance(units, list):
            persona_runs = [
                {
                    "unit_id": str(unit.get("unit_id", f"unit-{index + 1}"))
                    if isinstance(unit, dict)
                    else f"unit-{index + 1}",
                    "status": "ran",
                    "reason": "Planned for execution.",
                }
                for index, unit in enumerate(units)
            ]
    payload = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "target": str(target),
        "selections": plan.get("selections", {}),
        "tool_setup_results": plan.get("tool_setup_results", []),
        "tool_setup_error": plan.get("tool_setup_error"),
        "tool_review_results": plan.get("tool_review_results", []),
        "tool_review_error": plan.get("tool_review_error"),
        "persona_runs": persona_runs,
        "feedback_actions": plan.get("feedback_actions", []),
        "findings": plan.get("findings", []),
        "feedback": plan.get("feedback", []),
    }
    report_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return report_path


def update_feedback_state(*, target: Path, plan: dict) -> Path:
    state_path = default_feedback_state_path(target=target)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()

    state: dict = {"schema_version": CURRENT_SCHEMA_VERSION, "items": {}, "updated_at": now}
    if state_path.exists():
        loaded = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            state = loaded
    items = state.setdefault("items", {})
    if not isinstance(items, dict):
        raise TypeError("Feedback state file is invalid: 'items' must be an object.")

    active_ids: set[str] = set()
    for raw in plan.get("feedback_actions", []):
        if not isinstance(raw, dict):
            continue
        item_id = raw.get("id")
        if not isinstance(item_id, str) or not item_id:
            continue
        active_ids.add(item_id)
        existing = items.get(item_id, {})
        if not isinstance(existing, dict):
            existing = {}

        status = existing.get("status", "open")
        if status not in FEEDBACK_STATUSES:
            status = "open"
        seen_count = existing.get("seen_count", 0)
        if not isinstance(seen_count, int):
            seen_count = 0

        items[item_id] = {
            "id": item_id,
            "title": raw.get("title", ""),
            "priority": raw.get("priority", "P3"),
            "action": raw.get("action", ""),
            "why": raw.get("why", ""),
            "status": status,
            "active": True,
            "first_seen": existing.get("first_seen", now),
            "last_seen": now,
            "seen_count": seen_count + 1,
        }

    for item_id, item in items.items():
        if not isinstance(item, dict):
            continue
        if item_id not in active_ids:
            item["active"] = False

    state["active_context"] = _build_active_context(items)
    state["schema_version"] = CURRENT_SCHEMA_VERSION
    state["updated_at"] = now
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return state_path


def write_feedback_learning_queue(*, target: Path) -> Path:
    state_path = default_feedback_state_path(target=target)
    if not state_path.exists():
        raise ValueError("Feedback state file does not exist yet. Run a review first.")

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("Feedback state file is invalid.")
    items = payload.get("items", {})
    if not isinstance(items, dict):
        raise TypeError("Feedback state file is invalid: 'items' must be an object.")

    candidates: list[dict] = []
    for item in items.values():
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "open"))
        if status not in {"open", "in_progress", "accepted"}:
            continue
        if not item.get("active"):
            continue
        candidate = {
            "id": str(item.get("id", "")),
            "priority": str(item.get("priority", "P3")),
            "title": str(item.get("title", "")),
            "action": str(item.get("action", "")),
            "why": str(item.get("why", "")),
            "status": status,
            "last_seen": str(item.get("last_seen", "")),
            "seen_count": item.get("seen_count", 0),
        }
        candidates.append(candidate)

    candidates.sort(
        key=lambda item: (
            PRIORITY_WEIGHT.get(item.get("priority", "P3"), 99),
            item.get("id", ""),
        )
    )

    queue_payload = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "target": str(target),
        "source_feedback_state_path": str(state_path),
        "items": candidates,
    }
    queue_path = default_feedback_learning_queue_path(target=target)
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(json.dumps(queue_payload, indent=2) + "\n", encoding="utf-8")
    return queue_path


def promote_accepted_feedback_to_learnings(*, target: Path, feedback_ids: list[str] | None = None) -> dict:
    state_path = default_feedback_state_path(target=target)
    if not state_path.exists():
        raise ValueError("Feedback state file does not exist yet. Run a review first.")

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("Feedback state file is invalid.")
    items = payload.get("items", {})
    if not isinstance(items, dict):
        raise TypeError("Feedback state file is invalid: 'items' must be an object.")

    allowed_ids = {item.strip() for item in (feedback_ids or []) if isinstance(item, str) and item.strip()}
    promoted_ids: list[str] = []
    learned_practices: list[str] = []
    now = datetime.now(UTC).isoformat()
    for item_id, item in items.items():
        if not isinstance(item, dict):
            continue
        if allowed_ids and item_id not in allowed_ids:
            continue
        if item.get("status") != "accepted":
            continue
        if not item.get("active"):
            continue
        action_text = str(item.get("action", "")).strip()
        title_text = str(item.get("title", "")).strip()
        if action_text:
            learned_practices.append(action_text)
        elif title_text:
            learned_practices.append(title_text)
        promoted_ids.append(item_id)
        item["status"] = "done"
        item["status_updated_at"] = now
        item["promoted_to_learnings_at"] = now
        item["active"] = False

    if learned_practices:
        record_learned_practices(target=target, practices=learned_practices)
    payload["active_context"] = _build_active_context(items)
    payload["updated_at"] = now
    state_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    queue_path = write_feedback_learning_queue(target=target)
    return {
        "promoted_ids": promoted_ids,
        "learned_practices": learned_practices,
        "feedback_state_path": str(state_path),
        "feedback_learning_queue_path": str(queue_path),
    }


def apply_feedback_status_updates(*, target: Path, updates: list[str]) -> Path:
    state_path = default_feedback_state_path(target=target)
    if not state_path.exists():
        raise ValueError("Feedback state file does not exist yet. Run a review first.")

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("Feedback state file is invalid.")
    items = payload.get("items", {})
    if not isinstance(items, dict):
        raise TypeError("Feedback state file is invalid: 'items' must be an object.")

    now = datetime.now(UTC).isoformat()
    for update in updates:
        if ":" not in update:
            raise ValueError(f"Invalid feedback status update '{update}'. Use <id>:<status>.")
        item_id, status = update.split(":", 1)
        item_id = item_id.strip()
        status = status.strip()
        if status not in FEEDBACK_STATUSES:
            allowed = ", ".join(sorted(FEEDBACK_STATUSES))
            raise ValueError(f"Invalid feedback status '{status}'. Allowed: {allowed}.")
        if item_id not in items or not isinstance(items[item_id], dict):
            raise ValueError(f"Unknown feedback id '{item_id}'.")
        items[item_id]["status"] = status
        items[item_id]["status_updated_at"] = now

    payload["active_context"] = _build_active_context(items)
    payload["updated_at"] = now
    state_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return state_path


def state_to_wizard_config(state: dict) -> dict:
    selections = state.get("selections", {})
    if not isinstance(selections, dict):
        return {}
    config: dict[str, object] = {}
    for key in (
        "personas",
        "baselines",
        "baseline_practices",
        "tools",
        "languages",
        "language_practices",
        "specialties",
        "strategies",
    ):
        value = selections.get(key)
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            config[key] = value
    harness = state.get("harness")
    if isinstance(harness, str) and harness:
        config["harness"] = harness
    return config


def extract_state_payload(*, harness: str, command_name: str, plan: dict) -> dict:
    payload = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "harness": harness,
        "command_name": command_name,
        "selections": plan["selections"],
    }
    setup_tool_policy = plan.get("setup_tool_policy")
    if isinstance(setup_tool_policy, dict):
        payload["setup_tool_policy"] = setup_tool_policy
    return payload


def build_uninstall_paths(*, target: Path, name: str) -> list[Path]:
    return [
        target / ".github" / "agents" / f"{name}.agent.md",
        target / ".github" / "instructions" / f"{name}.instructions.md",
        target / ".github" / "prompts" / f"{name}.prompt.md",
        target / ".github" / "copilot-instructions.md",
        default_state_path(target=target),
    ]


def apply_uninstall(*, target: Path, name: str) -> UninstallResult:
    removed: list[Path] = []
    skipped: list[Path] = []
    for path in build_uninstall_paths(target=target, name=name):
        relative = path.relative_to(target)
        if not path.exists():
            skipped.append(relative)
            continue
        path.unlink()
        removed.append(relative)
    for folder in (
        target / ".github" / "agents",
        target / ".github" / "instructions",
        target / ".github" / "prompts",
        target / ".github",
        target / ".code-review",
    ):
        if folder.exists() and not any(folder.iterdir()):
            folder.rmdir()
    return UninstallResult(target=target, removed=removed, skipped=skipped)


def render_uninstall_result(result: UninstallResult, *, harness: str, name: str) -> str:
    lines = [
        "crk uninstall",
        "=====================",
        "",
        f"Harness: {harness}",
        f"Command name: /{name}",
        f"Target: {result.target}",
        "",
        "Files",
        "-----",
        "",
        *(
            [f"- removed: {path}" for path in result.removed] + [f"- not found: {path}" for path in result.skipped]
            or ["- (none)"]
        ),
    ]
    return "\n".join(lines) + "\n"


def uninstall_commands_for_tools(*, tool_ids: list[str], tools: dict[str, object]) -> list[tuple[str, str]]:
    commands: list[tuple[str, str]] = []
    for tool_id in tool_ids:
        tool = tools.get(tool_id)
        if not tool:
            continue
        uninstall = getattr(tool, "uninstall", [])
        for command in uninstall:
            commands.append((tool_id, command))
    return commands


def _approve_setup_command(
    *,
    command: str,
    approval_policy: dict,
    interactive: bool,
) -> bool:
    approved_commands = approval_policy.setdefault("approved_commands", [])
    if not isinstance(approved_commands, list):
        approval_policy["approved_commands"] = []
        approved_commands = approval_policy["approved_commands"]
    if command in approved_commands:
        return True

    mode = approval_policy.get("mode", "prompt")
    if mode == "allow-selected":
        approved_commands.append(command)
        return True

    if not interactive:
        return False

    answer = input(f"Allow setup tool command? `{command}` [y/N/a]: ").strip().lower()
    if answer in {"a", "all"}:
        approval_policy["mode"] = "allow-selected"
        approved_commands.append(command)
        return True
    if answer in {"y", "yes"}:
        approved_commands.append(command)
        return True
    return False


def _all_verify_commands_pass(
    commands: list[str],
    command_environment: dict[str, str] | None,
) -> bool:
    """Silently probe verification commands to check if a tool is already installed."""
    for command in commands:
        if not isinstance(command, str):
            continue
        result = _run_shell_command_with_environment(
            command=command,
            interactive=False,
            env=command_environment,
        )
        if result.returncode != 0:
            return False
    return bool(commands)


def run_selected_tool_setup(
    *,
    deterministic_gates: list[dict],
    approval_policy: dict | None = None,
    interactive: bool | None = None,
    command_environment: dict[str, str] | None = None,
) -> tuple[list[dict], str | None]:
    platform = platform_label()
    results: list[dict] = []
    policy = (
        approval_policy if isinstance(approval_policy, dict) else {"mode": "allow-selected", "approved_commands": []}
    )
    if interactive is None:
        interactive = bool(sys.stdin.isatty() and sys.stdout.isatty())

    for gate in deterministic_gates:
        tool_id = str(gate.get("id", ""))
        title = str(gate.get("title", tool_id))
        gate_result = {"id": tool_id, "title": title, "steps": [], "status": "passed"}

        verify_commands = [c for c in gate.get("commands", []) if isinstance(c, str)]
        if gate.get("setup") and _all_verify_commands_pass(verify_commands, command_environment):
            gate_result["steps"].append({"kind": "verify", "text": "already-installed", "status": "passed"})
            results.append(gate_result)
            continue

        for note in gate.get("setup", []):
            if not isinstance(note, str):
                continue
            command = _command_for_platform(note, platform)
            if command is None:
                if note.strip() == "uv installed":
                    if shutil.which("uv") is None:
                        gate_result["steps"].append({"kind": "prereq", "text": note, "status": "failed"})
                        gate_result["status"] = "failed"
                        results.append(gate_result)
                        return results, "Tool setup requires `uv`, but it is not installed or not on PATH."
                    gate_result["steps"].append({"kind": "prereq", "text": note, "status": "passed"})
                continue
            if not _approve_setup_command(command=command, approval_policy=policy, interactive=interactive):
                gate_result["steps"].append({"kind": "setup", "text": command, "status": "blocked"})
                gate_result["status"] = "failed"
                results.append(gate_result)
                return (
                    results,
                    (
                        "Tool setup command was not approved. Rerun `init` and use "
                        "`--tool-approval allow-selected` or approve commands interactively."
                    ),
                )
            completed = _execute_setup_command(
                command=command,
                interactive=interactive,
                phase="install",
                tool_id=tool_id,
                command_environment=command_environment,
            )
            step_result = {
                "kind": "setup",
                "text": command,
                "status": "passed" if completed.returncode == 0 else "failed",
                "exit_code": completed.returncode,
                "stderr": completed.stderr.strip(),
            }
            gate_result["steps"].append(step_result)
            if completed.returncode != 0:
                gate_result["status"] = "failed"
                results.append(gate_result)
                return results, f"Tool setup failed for {tool_id}: {command}"

        for command in gate.get("commands", []):
            if not isinstance(command, str):
                continue
            if not _approve_setup_command(command=command, approval_policy=policy, interactive=interactive):
                gate_result["steps"].append({"kind": "verify", "text": command, "status": "blocked"})
                gate_result["status"] = "failed"
                results.append(gate_result)
                return (
                    results,
                    (
                        "Tool verification command was not approved. Rerun `init` and use "
                        "`--tool-approval allow-selected` or approve commands interactively."
                    ),
                )
            completed = _execute_setup_command(
                command=command,
                interactive=interactive,
                phase="setup",
                tool_id=tool_id,
                command_environment=command_environment,
            )
            step_result = {
                "kind": "verify",
                "text": command,
                "status": "passed" if completed.returncode == 0 else "failed",
                "exit_code": completed.returncode,
                "stderr": completed.stderr.strip(),
            }
            gate_result["steps"].append(step_result)
            if completed.returncode != 0:
                gate_result["status"] = "failed"
                results.append(gate_result)
                return results, f"Tool verification failed for {tool_id}: {command}"

        results.append(gate_result)

    return results, None


def run_deterministic_gates(
    *,
    target: Path,
    deterministic_gates: list[dict],
    interactive: bool | None = None,
    command_environment: dict[str, str] | None = None,
) -> tuple[list[dict], str | None]:
    results: list[dict] = []
    if interactive is None:
        interactive = bool(sys.stdin.isatty() and sys.stdout.isatty())

    def _git_stdout(command: list[str]) -> str | None:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            return None
        candidate = completed.stdout.strip()
        return candidate or None

    def resolve_gitleaks_log_opts() -> str | None:
        upstream = _git_stdout(
            ["git", "-C", str(target), "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]
        )
        if upstream:
            merge_base = _git_stdout(["git", "-C", str(target), "merge-base", upstream, "HEAD"])
            if merge_base:
                return f"{merge_base}..HEAD"

        default_remote_head = _git_stdout(["git", "-C", str(target), "symbolic-ref", "refs/remotes/origin/HEAD"])
        if default_remote_head:
            merge_base = _git_stdout(["git", "-C", str(target), "merge-base", default_remote_head, "HEAD"])
            if merge_base:
                return f"{merge_base}..HEAD"

        has_previous_commit = _git_stdout(["git", "-C", str(target), "rev-parse", "--verify", "HEAD~1"]) is not None
        if has_previous_commit:
            return "HEAD~1..HEAD"
        return None

    def resolve_review_command(command: str) -> str:
        if "gitleaks detect" not in command or "--log-opts HEAD~1..HEAD" not in command:
            return command
        log_opts = resolve_gitleaks_log_opts()
        if log_opts is None:
            return command.replace(" --log-opts HEAD~1..HEAD", "")
        return command.replace("HEAD~1..HEAD", log_opts)

    for gate in deterministic_gates:
        tool_id = str(gate.get("id", ""))
        title = str(gate.get("title", tool_id))
        gate_result = {"id": tool_id, "title": title, "steps": [], "status": "passed"}
        commands = gate.get("review_commands", gate.get("commands", []))
        for command in commands:
            if not isinstance(command, str):
                continue
            resolved_command = resolve_review_command(command)
            if interactive:
                completed = _run_shell_command_with_environment(
                    command=resolved_command,
                    cwd=target,
                    phase="review",
                    tool_id=tool_id,
                    interactive=True,
                    env=command_environment,
                )
            else:
                completed = _run_shell_command_with_environment(
                    command=resolved_command,
                    cwd=target,
                    interactive=False,
                    env=command_environment,
                )
            step_result = {
                "kind": "review",
                "text": resolved_command,
                "status": "passed" if completed.returncode == 0 else "failed",
                "exit_code": completed.returncode,
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
            }
            gate_result["steps"].append(step_result)
            if completed.returncode != 0:
                gate_result["status"] = "failed"
        results.append(gate_result)

    error = None
    failed_gate = next((result for result in results if result.get("status") == "failed"), None)
    if isinstance(failed_gate, dict):
        failed_step = next(
            (
                step
                for step in failed_gate.get("steps", [])
                if isinstance(step, dict) and step.get("status") == "failed" and step.get("text")
            ),
            None,
        )
        if isinstance(failed_step, dict) and failed_step.get("text"):
            error = f"Tool review failed for {failed_gate.get('id', 'deterministic-tool-gate')}: {failed_step['text']}"
        else:
            error = f"Tool review failed for {failed_gate.get('id', 'deterministic-tool-gate')}"
    return results, error


def tool_setup_feedback(results: list[dict], error: str | None) -> tuple[list[dict], list[str]]:
    actions: list[dict] = []
    feedback: list[str] = []

    if error is None:
        return actions, feedback

    failed_result = next((result for result in results if result.get("status") == "failed"), None)
    tool_id = (
        str(failed_result.get("id", "deterministic-tool-gate"))
        if isinstance(failed_result, dict)
        else "deterministic-tool-gate"
    )
    title = f"Deterministic tool gate failed for {tool_id}"
    action = error
    if isinstance(failed_result, dict):
        failed_step = next(
            (
                step
                for step in failed_result.get("steps", [])
                if isinstance(step, dict) and step.get("status") == "failed" and step.get("text")
            ),
            None,
        )
        if isinstance(failed_step, dict) and failed_step.get("text"):
            action = f"Fix `{failed_step['text']}` and rerun the selected deterministic gate."

    actions.append(
        {
            "id": "deterministic-tool-gate-failed",
            "priority": "P1",
            "title": title,
            "action": action,
            "why": error,
        }
    )
    feedback.append(f"[P1] {title} — Action: {action}")
    return actions, feedback


def tool_review_feedback(results: list[dict], error: str | None) -> tuple[list[dict], list[str]]:
    actions: list[dict] = []
    feedback: list[str] = []

    if error is None:
        return actions, feedback

    failed_result = next((result for result in results if result.get("status") == "failed"), None)
    tool_id = (
        str(failed_result.get("id", "deterministic-tool-gate"))
        if isinstance(failed_result, dict)
        else "deterministic-tool-gate"
    )
    title = f"Review gate failed for {tool_id}"
    action = error
    if isinstance(failed_result, dict):
        failed_step = next(
            (
                step
                for step in failed_result.get("steps", [])
                if isinstance(step, dict) and step.get("status") == "failed" and step.get("text")
            ),
            None,
        )
        if isinstance(failed_step, dict) and failed_step.get("text"):
            action = f"Fix `{failed_step['text']}` and rerun the selected review gate."

    actions.append(
        {
            "id": "deterministic-review-gate-failed",
            "priority": "P1",
            "title": title,
            "action": action,
            "why": error,
        }
    )
    feedback.append(f"[P1] {title} — Action: {action}")
    return actions, feedback


def compute_deselections(*, previous_state: dict, current_plan: dict) -> dict[str, list[str]]:
    previous = previous_state.get("selections", {}) if isinstance(previous_state, dict) else {}
    current = current_plan.get("selections", {}) if isinstance(current_plan, dict) else {}
    if not isinstance(previous, dict) or not isinstance(current, dict):
        return {}

    keys = ("personas", "baselines", "tools", "languages", "specialties", "strategies")
    deselections: dict[str, list[str]] = {}
    for key in keys:
        previous_values = previous.get(key, [])
        current_values = current.get(key, [])
        if not isinstance(previous_values, list) or not isinstance(current_values, list):
            continue
        removed = [value for value in previous_values if value not in current_values]
        if removed:
            deselections[key] = removed
    return deselections


def render_deselection_summary(deselections: dict[str, list[str]]) -> str:
    lines = [
        "## Deselection cleanup summary",
        "",
        "| Selection area | Deselected values |",
        "|---|---|",
    ]
    if not deselections:
        lines.append("| (none) | No prior selections were removed. |")
    else:
        for key in ("personas", "baselines", "languages", "specialties", "tools", "strategies"):
            values = deselections.get(key, [])
            if values:
                lines.append(f"| {key} | `{', '.join(values)}` |")
    return "\n".join(lines) + "\n"


def run_uninstall_commands(commands: list[tuple[str, str]], *, interactive: bool = False) -> list[tuple[str, str, int]]:
    results: list[tuple[str, str, int]] = []
    for tool_id, command in commands:
        if interactive and sys.stdin.isatty() and sys.stdout.isatty():
            completed = _run_shell_command_with_spinner(command, phase="uninstall", tool_id=tool_id)
        else:
            completed = subprocess.run(shlex.split(command), check=False, capture_output=True, text=True)
        results.append((tool_id, command, completed.returncode))
    return results


def render_uninstall_commands(commands: list[tuple[str, str]]) -> str:
    lines = [
        "## Deselected tool uninstall commands",
        "",
        "| Tool | Command |",
        "|---|---|",
    ]
    if not commands:
        lines.append("| (none) | No uninstall commands available for deselected tool packs. |")
    else:
        for tool_id, command in commands:
            lines.append(f"| `{tool_id}` | `{command}` |")
    return "\n".join(lines) + "\n"


def run_bootstrap_with_status(*, target: Path, harness: str, name: str) -> BootstrapResult:
    result = BootstrapResult(target=target, created=[], updated=[], skipped=[])
    artifacts = build_bootstrap_artifacts(target=target, name=name)

    if not sys.stdout.isatty() or not sys.stdin.isatty():
        result = apply_bootstrap(target=target, name=name)
        print(colorize_console_block(render_bootstrap_result(result, harness=harness, name=name, wizard_started=False)))
        return result

    print(_color("crk setup", f"{BOLD}{CYAN}"))
    print(_color("=================", CYAN))
    print(f"Harness: {harness}")
    print(f"Target: {target}")
    print()

    for artifact in artifacts:
        relative = artifact.path.relative_to(target)
        for frame in SPINNER_FRAMES:
            sys.stdout.write(f"\r{frame} Preparing `{relative}`")
            sys.stdout.flush()
            time.sleep(0.04)
        artifact.path.parent.mkdir(parents=True, exist_ok=True)
        if artifact.path.exists():
            current = artifact.path.read_text(encoding="utf-8")
            if current == artifact.content:
                result.skipped.append(relative)
                sys.stdout.write(f"\r{_color(CHECKMARK, GREEN)} {_color('Unchanged', DIM)} `{relative}`{' ' * 20}\n")
            else:
                artifact.path.write_text(artifact.content, encoding="utf-8")
                result.updated.append(relative)
                sys.stdout.write(f"\r{_color(CHECKMARK, GREEN)} {_color('Updated', YELLOW)} `{relative}`{' ' * 20}\n")
        else:
            artifact.path.write_text(artifact.content, encoding="utf-8")
            result.created.append(relative)
            sys.stdout.write(f"\r{_color(CHECKMARK, GREEN)} {_color('Created', BLUE)} `{relative}`{' ' * 20}\n")
        sys.stdout.flush()

    print()
    print(colorize_console_block(render_bootstrap_result(result, harness=harness, name=name, wizard_started=False)))
    return result


def render_bootstrap_result(result: BootstrapResult, *, harness: str, name: str, wizard_started: bool) -> str:
    lines = [
        "crk bootstrap",
        "=====================",
        "",
        f"Harness: {harness}",
        f"Command name: /{name}",
        f"Target: {result.target}",
        "",
        "Files",
        "-----",
        "",
        *(
            [f"- created: {path}" for path in result.created]
            + [f"- updated: {path}" for path in result.updated]
            + [f"- unchanged: {path}" for path in result.skipped]
            or ["- (none)"]
        ),
        "",
        "Next step",
        "---------",
        "",
        f"Open your selected harness in this repository and use the {name} review workflow.",
    ]
    if wizard_started:
        lines.extend(["", "The wizard will launch next in this session."])
    else:
        lines.extend(
            [
                "",
                "If you want the interactive wizard now, re-run init from an interactive terminal or use --wizard.",
            ]
        )
    return "\n".join(lines) + "\n"


def pause_for_acknowledgement(message: str) -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return
    print()
    print(_color("=" * 72, CYAN))
    print(_color(message.strip(), f"{BOLD}{GREEN}"))
    print()
    print(_color(">>> Press Enter to continue <<<", f"{BOLD}{MAGENTA}"))
    print(_color("=" * 72, CYAN))
    input()


def should_start_review_after_init(*, harness: str, action: str) -> bool:
    if action == "start":
        return True
    if action == "exit":
        return False
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return False
    answer = input("Start crk workflow now? [Y/n]: ").strip().lower()
    return answer in {"", "y", "yes"}


def choose_review_workflow_after_init(*, harness: str, workflow: str) -> str:
    if workflow in {"dev-loop", "pr-review"}:
        return workflow
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return "dev-loop"
    answer = input("Choose workflow: [D]ev loop or [P]R review? [D/p]: ").strip().lower()
    if answer in {"p", "pr", "pr-review"}:
        return "pr-review"
    return "dev-loop"


def choose_pull_request_reference(*, provided: str | None = None) -> str | None:
    if provided:
        candidate = provided.strip()
        return candidate or None
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None
    answer = input("PR number or URL (blank = active branch PR): ").strip()
    return answer or None


def _format_table(rows: list[tuple[str, str]]) -> str:
    width = max(len(label) for label, _ in rows)
    lines = ["| Item | Value |", "|---|---|"]
    for label, value in rows:
        lines.append(f"| {label.ljust(width)} | {value} |")
    return "\n".join(lines)


def _format_file_table(entries: list[tuple[str, str]]) -> str:
    if not entries:
        return "| File | Status |\n|---|---|\n| (none) | - |"
    width = max(len(path) for path, _ in entries)
    lines = ["| File | Status |", "|---|---|"]
    for path, status in entries:
        lines.append(f"| {path.ljust(width)} | {status} |")
    return "\n".join(lines)


def render_plan_summary(plan: dict) -> str:
    selections = plan["selections"]
    token_strategy = plan["token_strategy"]
    lines = [
        "crk review summary",
        "========================",
        "",
        f"Target: {plan['target']}",
        f"Personas: {', '.join(selections['personas']) or 'none'}",
        f"Baselines: {', '.join(selections['baselines']) or 'none'}",
        f"Baseline practices: {len(selections.get('baseline_practices', []))}",
        f"Tools: {', '.join(selections['tools']) or 'none'}",
        f"Languages: {', '.join(selections['languages']) or 'none'}",
        f"Language practices: {len(selections.get('language_practices', []))}",
        f"Specialties: {', '.join(selections['specialties']) or 'none'}",
        f"Strategies: {', '.join(selections['strategies']) or 'none'}",
        f"Units: {len(plan['units'])}",
        f"Model routing: {token_strategy['model_routing']}",
        f"Cache mode: {token_strategy['cache_mode']}",
        "",
        "## Tooling setup",
        "",
        "Each selected tool pack lists install/prep prerequisites first, then the verification commands that should run after setup.",
        "",
        "### Selected tool packs",
        "",
    ]
    gates = plan.get("deterministic_gates", [])
    if gates:
        for gate in gates:
            lines.extend(
                [
                    f"- **{gate['id']}** (`{gate['category']}`): {gate['why']}",
                    *[f"  - prerequisite/setup: `{step}`" for step in gate.get("setup", [])],
                    *[f"  - verify: `{command}`" for command in gate.get("commands", [])],
                ]
            )
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def render_init_plan(*, harness: str, name: str, emit: str = "markdown") -> str:
    if harness not in HARNESS_PROFILES:
        available = ", ".join(sorted(HARNESS_PROFILES))
        raise ValueError(f"Unknown harness: {harness}. Available: {available}")

    profile = HARNESS_PROFILES[harness]
    if emit == "json":
        import json

        return json.dumps(
            {
                "harness": harness,
                "harness_name": profile.name,
                "command_name": name,
                "summary": profile.summary,
                "notes": profile.notes,
            },
            indent=2,
        )

    lines = [
        "# crk init",
        "",
        _format_table(
            [
                ("Harness", profile.name),
                ("Command name", f"`{name}`"),
                ("Default install name", f"`{profile.command_name}`"),
            ]
        ),
        "",
        "## Notes",
        "",
        *[f"- {note}" for note in profile.notes],
        "",
        "## Next step",
        "",
        f"Install the repo bootstrap files so the harness can discover `/{name}` as a repo-local agent and then launch the wizard.",
    ]
    return "\n".join(lines) + "\n"
