from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol


class ExecutionPlugin(Protocol):
    id: str

    def run_setup(
        self,
        *,
        deterministic_gates: list[dict],
        interactive: bool | None = None,
        command_environment: dict[str, str] | None = None,
        approval_policy: dict | None = None,
    ) -> tuple[list[dict], str | None]: ...

    def run_review(
        self,
        *,
        target: Path,
        deterministic_gates: list[dict],
        interactive: bool | None = None,
        command_environment: dict[str, str] | None = None,
    ) -> tuple[list[dict], str | None]: ...


class ShellExecutionPlugin:
    id = "shell-local"

    def __init__(
        self,
        *,
        run_selected_tool_setup: Callable[..., tuple[list[dict], str | None]],
        run_deterministic_gates: Callable[..., tuple[list[dict], str | None]],
    ) -> None:
        self._run_selected_tool_setup = run_selected_tool_setup
        self._run_deterministic_gates = run_deterministic_gates

    def run_setup(
        self,
        *,
        deterministic_gates: list[dict],
        interactive: bool | None = None,
        command_environment: dict[str, str] | None = None,
        approval_policy: dict | None = None,
    ) -> tuple[list[dict], str | None]:
        try:
            return self._run_selected_tool_setup(
                deterministic_gates=deterministic_gates,
                interactive=interactive,
                command_environment=command_environment,
                approval_policy=approval_policy,
            )
        except TypeError:
            return self._run_selected_tool_setup(deterministic_gates=deterministic_gates)

    def run_review(
        self,
        *,
        target: Path,
        deterministic_gates: list[dict],
        interactive: bool | None = None,
        command_environment: dict[str, str] | None = None,
    ) -> tuple[list[dict], str | None]:
        try:
            return self._run_deterministic_gates(
                target=target,
                deterministic_gates=deterministic_gates,
                interactive=interactive,
                command_environment=command_environment,
            )
        except TypeError:
            return self._run_deterministic_gates(target=target, deterministic_gates=deterministic_gates)


@dataclass
class ExecutionPluginRegistry:
    plugins: dict[str, ExecutionPlugin]

    def resolve(self, plugin_id: str | None) -> ExecutionPlugin:
        selected = (plugin_id or "").strip()
        if selected:
            plugin = self.plugins.get(selected)
            if plugin is None:
                available = ", ".join(sorted(self.plugins))
                raise ValueError(f"Unknown execution plugin '{selected}'. Available: {available}")
            return plugin
        return self.plugins["shell-local"]


def build_default_execution_registry(
    *,
    run_selected_tool_setup: Callable[..., tuple[list[dict], str | None]],
    run_deterministic_gates: Callable[..., tuple[list[dict], str | None]],
) -> ExecutionPluginRegistry:
    shell_plugin = ShellExecutionPlugin(
        run_selected_tool_setup=run_selected_tool_setup,
        run_deterministic_gates=run_deterministic_gates,
    )
    return ExecutionPluginRegistry(plugins={"shell-local": shell_plugin})
