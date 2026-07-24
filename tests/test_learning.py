from __future__ import annotations

import ast
from pathlib import Path

import pytest

from code_review.review_planner.learning import (
    default_learned_practices_path,
    merge_learned_extensions,
    record_learned_practices,
)


def _read_source(relative_path: str) -> str:
    repo_root = Path(__file__).resolve().parents[1]
    return (repo_root / relative_path).read_text(encoding="utf-8")


def _collect_import_from(source: str) -> dict[str, set[str]]:
    """Maps each imported module to the names imported from it.

    Handles `from x import y` (keyed by `x`), `from . import x` (keyed by the
    submodule name `x` itself, since the alias IS the module), and bare
    `import x.y` (keyed by the dotted path itself) — so a boundary check can't
    be defeated by switching import styles.
    """
    tree = ast.parse(source)
    imports: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                imports.setdefault(node.module, set()).update(alias.name for alias in node.names)
            else:
                for alias in node.names:
                    imports.setdefault(alias.name, set()).add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.setdefault(alias.name, set()).add(alias.name)
    return imports


def test_record_learned_practices_is_owned_by_learning_module() -> None:
    assert record_learned_practices.__module__ == "code_review.review_planner.learning"


def test_merge_learned_extensions_is_owned_by_learning_module() -> None:
    assert merge_learned_extensions.__module__ == "code_review.review_planner.learning"


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


@pytest.mark.parametrize(
    "name",
    [
        "merge_learned_extensions",
        "default_learned_practices_path",
        "load_learned_practices",
        "save_learned_practices",
        "LEARNED_PRACTICES_FILE",
    ],
)
def test_relocated_names_not_importable_from_init(name: str) -> None:
    with pytest.raises(ImportError):
        exec(f"from code_review.review_planner.init import {name}")  # noqa: S102


def test_learning_module_has_no_cross_import() -> None:
    learning_imports = _collect_import_from(_read_source("src/code_review/review_planner/learning.py"))
    assert not any(module.endswith("init") for module in learning_imports)
    assert not any(module.endswith("render") for module in learning_imports)

    init_imports = _collect_import_from(_read_source("src/code_review/review_planner/init.py"))
    assert not any(module.endswith("render") for module in init_imports)

    init_learning_names: set[str] = set()
    for module, names in init_imports.items():
        if module.endswith("learning"):
            init_learning_names.update(names)
    assert init_learning_names == {"record_learned_practices"}


def test_render_module_only_imports_platform_label_from_init() -> None:
    render_imports = _collect_import_from(_read_source("src/code_review/review_planner/render.py"))

    render_init_names: set[str] = set()
    for module, names in render_imports.items():
        if module.endswith("init"):
            render_init_names.update(names)
    assert render_init_names == {"platform_label"}


def test_cli_imports_learned_practices_from_learning_module() -> None:
    cli_imports = _collect_import_from(_read_source("src/code_review/cli.py"))

    learning_names: set[str] = set()
    init_names: set[str] = set()
    for module, names in cli_imports.items():
        if module.endswith("review_planner.learning"):
            learning_names.update(names)
        elif module.endswith("review_planner.init"):
            init_names.update(names)

    assert "merge_learned_extensions" in learning_names
    assert "record_learned_practices" in learning_names
    assert "merge_learned_extensions" not in init_names
    assert "record_learned_practices" not in init_names
