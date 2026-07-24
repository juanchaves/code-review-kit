from __future__ import annotations

import ast
from pathlib import Path

import pytest

from code_review.review_planner.learning import (
    default_learned_practices_path,
    merge_learned_extensions,
    record_learned_practices,
)


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
    ],
)
def test_relocated_names_not_importable_from_init(name: str) -> None:
    with pytest.raises(ImportError):
        exec(f"from code_review.review_planner.init import {name}")  # noqa: S102


def test_learning_module_has_no_cross_import() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    learning_source = (repo_root / "src/code_review/review_planner/learning.py").read_text(encoding="utf-8")
    init_source = (repo_root / "src/code_review/review_planner/init.py").read_text(encoding="utf-8")

    learning_tree = ast.parse(learning_source)
    learning_modules = {
        node.module for node in ast.walk(learning_tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(module.endswith("init") for module in learning_modules)
    assert not any(module.endswith("render") for module in learning_modules)

    init_tree = ast.parse(init_source)
    init_modules = {node.module for node in ast.walk(init_tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert not any(module.endswith("render") for module in init_modules)

    init_learning_names: set[str] = set()
    for node in ast.walk(init_tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("learning"):
            init_learning_names.update(alias.name for alias in node.names)
    assert init_learning_names == {"record_learned_practices"}


def test_cli_imports_learned_practices_from_learning_module() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    cli_source = (repo_root / "src/code_review/cli.py").read_text(encoding="utf-8")
    tree = ast.parse(cli_source)

    learning_names: set[str] = set()
    init_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.endswith("review_planner.learning"):
                learning_names.update(alias.name for alias in node.names)
            elif node.module.endswith("review_planner.init"):
                init_names.update(alias.name for alias in node.names)

    assert "merge_learned_extensions" in learning_names
    assert "record_learned_practices" in learning_names
    assert "merge_learned_extensions" not in init_names
    assert "record_learned_practices" not in init_names
