from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Self


@dataclass
class SandboxSession:
    id: str
    environment: dict[str, str]
    note: str | None = None
    cleanup: Callable[[], None] = field(repr=False, default=lambda: None)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.cleanup()
        return False


class SandboxPlugin(Protocol):
    id: str

    def enter(self, *, target: Path) -> SandboxSession: ...


class PassthroughSandbox:
    id = "passthrough"

    def enter(self, *, target: Path) -> SandboxSession:
        return SandboxSession(id=self.id, environment={}, note="No sandbox isolation requested.")


class ScratchHomeSandbox:
    id = "scratch-home"

    def enter(self, *, target: Path) -> SandboxSession:
        tempdir = tempfile.TemporaryDirectory(prefix="crk-sandbox-")
        root = Path(tempdir.name)
        home = root / "home"
        config = home / ".config"
        cache = home / ".cache"
        state = home / ".local" / "state"
        data = home / ".local" / "share"
        scratch = root / "tmp"
        for path in (home, config, cache, state, data, scratch):
            path.mkdir(parents=True, exist_ok=True)
        environment = {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "XDG_CONFIG_HOME": str(config),
            "XDG_CACHE_HOME": str(cache),
            "XDG_STATE_HOME": str(state),
            "XDG_DATA_HOME": str(data),
            "TMPDIR": str(scratch),
            "TEMP": str(scratch),
            "TMP": str(scratch),
        }
        note = f"Scratch HOME and XDG state isolated under {root} for {target}."
        return SandboxSession(id=self.id, environment=environment, note=note, cleanup=tempdir.cleanup)


@dataclass
class SandboxRegistry:
    plugins: dict[str, SandboxPlugin]

    def resolve(self, plugin_id: str | None) -> SandboxPlugin:
        selected = (plugin_id or "").strip()
        if selected and selected != "auto":
            plugin = self.plugins.get(selected)
            if plugin is None:
                available = ", ".join(sorted(self.plugins))
                raise ValueError(f"Unknown sandbox plugin '{selected}'. Available: {available}")
            return plugin
        return self.plugins["scratch-home"]


def build_default_sandbox_registry() -> SandboxRegistry:
    return SandboxRegistry(
        plugins={
            "passthrough": PassthroughSandbox(),
            "scratch-home": ScratchHomeSandbox(),
        }
    )
