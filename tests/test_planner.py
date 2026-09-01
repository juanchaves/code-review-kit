from __future__ import annotations

from pathlib import Path

from code_review.review_planner import requirements as requirements_module
from code_review.review_planner.catalog import Pack, build_dynamic_catalog
from code_review.review_planner.planner import (
    attach_tool_evidence_to_units,
    build_plan,
    build_tool_evidence,
    build_unit_prompt_context,
    expand_specialty_hierarchy,
    infer_language_ids,
    infer_specialty_ids,
    infer_tool_ids,
)
from code_review.review_planner.render import to_markdown
from code_review.review_planner.requirements import (
    apply_grilling_refinement,
    apply_walkthrough_overrides,
    derive_requirements,
    detect_issue_provider,
)


def _catalog():
    return build_dynamic_catalog({})


def test_infer_language_and_specialty_ids_from_repo_layout(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "script.sh").write_text("", encoding="utf-8")
    (tmp_path / "src" / "index.js").write_text("", encoding="utf-8")
    (tmp_path / "src" / "component.ts").write_text("", encoding="utf-8")
    (tmp_path / "cdk").mkdir()
    (tmp_path / "cdk.json").write_text("{}", encoding="utf-8")
    (tmp_path / "cdk" / "app.ts").write_text("", encoding="utf-8")

    _, _, _, languages, specialties, _ = _catalog()

    assert infer_language_ids(tmp_path, languages) == ["typescript", "javascript", "python", "shell"]
    assert infer_specialty_ids(tmp_path, specialties) == ["cdk"]


def test_infer_tool_ids_matches_selected_languages_and_specialty() -> None:
    _, _, tools, _, _, _ = _catalog()

    selected = infer_tool_ids(
        selected_baselines=["methodology-core"],
        selected_languages=["python", "shell", "javascript", "typescript"],
        selected_specialties=["cdk"],
        tools=tools,
    )

    assert selected == [
        "python-ruff",
        "python-pyrefly",
        "python-bandit",
        "python-radon",
        "shell-shellcheck",
        "shell-shfmt",
        "shell-bats",
        "js-biome",
        "js-typescript-eslint",
        "js-oxlint",
        "security-semgrep",
        "security-osv-scanner",
        "security-gitleaks",
        "js-tsc",
    ]


def test_infer_tool_ids_excludes_python_radon_for_non_python_language() -> None:
    _, _, tools, _, _, _ = _catalog()

    selected = infer_tool_ids(
        selected_baselines=["code-smells-refactoring"],
        selected_languages=["typescript"],
        selected_specialties=[],
        tools=tools,
    )

    assert "python-radon" not in selected
    assert "complexity-lizard" in selected


def test_infer_specialty_ids_detects_ui_ux_files(tmp_path: Path) -> None:
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text("", encoding="utf-8")
    (tmp_path / "web" / "styles.css").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "review_tui.py").write_text("", encoding="utf-8")

    _, _, _, _, specialties, _ = _catalog()

    assert infer_specialty_ids(tmp_path, specialties) == ["ui-ux-cli-tui", "ui-ux-web", "ui-ux"]


def test_infer_specialty_ids_detects_aws_from_package_json(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"dependencies": {"@aws-sdk/client-s3": "^3.0.0"}}', encoding="utf-8")

    _, _, _, _, specialties, _ = _catalog()

    assert "aws" in infer_specialty_ids(tmp_path, specialties)


def test_infer_specialty_ids_detects_aws_from_python_requirements(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("boto3==1.34.0\n", encoding="utf-8")

    _, _, _, _, specialties, _ = _catalog()

    assert "aws" in infer_specialty_ids(tmp_path, specialties)


def test_infer_specialty_ids_detects_gcp_from_terraform_provider(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text('provider "google" {\n  project = "example"\n}\n', encoding="utf-8")

    _, _, _, _, specialties, _ = _catalog()

    assert "gcp" in infer_specialty_ids(tmp_path, specialties)


def test_infer_specialty_ids_detects_azure_from_bicep_file(tmp_path: Path) -> None:
    (tmp_path / "main.bicep").write_text(
        "resource sa 'Microsoft.Storage/storageAccounts@2021-04-01' = {}\n", encoding="utf-8"
    )

    _, _, _, _, specialties, _ = _catalog()

    assert "azure" in infer_specialty_ids(tmp_path, specialties)


def test_infer_specialty_ids_ignores_cloud_sdk_signals_in_node_modules(tmp_path: Path) -> None:
    ignored = tmp_path / "node_modules" / "@aws-sdk" / "client-s3"
    ignored.mkdir(parents=True)
    (ignored / "package.json").write_text('{"dependencies": {"@aws-sdk/client-s3": "^3.0.0"}}', encoding="utf-8")

    _, _, _, _, specialties, _ = _catalog()

    assert "aws" not in infer_specialty_ids(tmp_path, specialties)


def test_catalog_cloud_hierarchy_reparents_cdk_under_aws() -> None:
    _, _, _, _, specialties, _ = _catalog()

    assert specialties["cloud"].parent is None
    assert specialties["aws"].parent == "cloud"
    assert specialties["gcp"].parent == "cloud"
    assert specialties["azure"].parent == "cloud"
    assert specialties["cdk"].parent == "aws"


def test_catalog_aws_sub_specialties_are_children_of_aws() -> None:
    _, _, _, _, specialties, _ = _catalog()

    assert specialties["aws-destructive-ops"].parent == "aws"
    assert specialties["aws-iam"].parent == "aws"

    expanded = expand_specialty_hierarchy(["aws-destructive-ops", "aws-iam"], specialties)
    assert expanded == ["cloud", "aws", "aws-destructive-ops", "aws-iam"]


def test_expand_specialty_hierarchy_walks_multi_level_ancestry() -> None:
    specialties = {
        "cloud": Pack(title="Cloud", practices=[], file_hints=[]),
        "aws": Pack(title="AWS", practices=[], file_hints=[], parent="cloud"),
        "aws-iam": Pack(title="AWS IAM", practices=[], file_hints=[], parent="aws"),
    }

    result = expand_specialty_hierarchy(["aws-iam"], specialties)

    assert result == ["cloud", "aws", "aws-iam"]


def test_expand_specialty_hierarchy_deduplicates_shared_ancestors() -> None:
    specialties = {
        "cloud": Pack(title="Cloud", practices=[], file_hints=[]),
        "aws": Pack(title="AWS", practices=[], file_hints=[], parent="cloud"),
        "aws-iam": Pack(title="AWS IAM", practices=[], file_hints=[], parent="aws"),
        "aws-destructive-ops": Pack(title="AWS Destructive Ops", practices=[], file_hints=[], parent="aws"),
    }

    result = expand_specialty_hierarchy(["aws-iam", "aws-destructive-ops"], specialties)

    assert result == ["cloud", "aws", "aws-iam", "aws-destructive-ops"]


def test_expand_specialty_hierarchy_ignores_unknown_or_cyclic_parents() -> None:
    specialties = {
        "orphan": Pack(title="Orphan", practices=[], file_hints=[], parent="missing-parent"),
    }

    result = expand_specialty_hierarchy(["orphan"], specialties)

    assert result == ["orphan"]


def test_infer_language_ids_ignores_single_incidental_shell_file(tmp_path: Path) -> None:
    # A shell file under an ignored tool-config dir should not pull in the shell toolchain.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("", encoding="utf-8")
    (tmp_path / ".claude" / "hooks").mkdir(parents=True)
    (tmp_path / ".claude" / "hooks" / "post-format.sh").write_text("", encoding="utf-8")

    _, _, _, languages, _, _ = _catalog()

    result = infer_language_ids(tmp_path, languages)
    assert "shell" not in result
    assert "python" in result


def test_infer_language_ids_ignores_files_in_tool_config_dirs(tmp_path: Path) -> None:
    # Files under .claude, .cursor, .copilot should not influence language detection.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("", encoding="utf-8")
    for tool_dir in (".claude", ".cursor", ".copilot"):
        (tmp_path / tool_dir).mkdir()
        (tmp_path / tool_dir / "hook.sh").write_text("", encoding="utf-8")

    _, _, _, languages, _, _ = _catalog()

    result = infer_language_ids(tmp_path, languages)
    assert "shell" not in result


def test_infer_specialty_ids_detects_harness_context_and_docs_quality(tmp_path: Path) -> None:
    (tmp_path / ".github" / "prompts").mkdir(parents=True)
    (tmp_path / ".github" / "prompts" / "agent.prompt.md").write_text("x", encoding="utf-8")
    (tmp_path / "README.md").write_text("# readme", encoding="utf-8")
    (tmp_path / "SKILL.md").write_text("# skill", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# changelog", encoding="utf-8")

    _, _, _, _, specialties, _ = _catalog()

    detected = infer_specialty_ids(tmp_path, specialties)
    assert "harness-context-quality" in detected
    assert "docs-quality" in detected


def test_build_plan_on_polyglot_repo_selects_expected_defaults(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    (tmp_path / "build.sh").write_text("", encoding="utf-8")
    (tmp_path / "index.js").write_text("", encoding="utf-8")
    (tmp_path / "component.ts").write_text("", encoding="utf-8")
    (tmp_path / "cdk.json").write_text("{}", encoding="utf-8")

    personas, baselines, tools, languages, specialties, strategies = _catalog()
    plan = build_plan(
        target=str(tmp_path),
        personas=personas,
        baselines=baselines,
        tools=tools,
        languages=languages,
        specialties=specialties,
        strategies=strategies,
        config={},
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
        token_policy={
            "profile": "balanced",
            "toon": False,
            "cache_mode": "prompt",
            "model_routing": "right-size",
            "max_parallel_units": 6,
            "max_files_per_unit": 120,
            "max_file_hints": 12,
        },
        strategy_mode="overlay",
    )

    assert plan["selections"]["languages"] == ["javascript", "python", "shell", "typescript"]
    assert plan["selections"]["specialties"] == ["cloud", "aws", "cdk"]
    assert plan["selections"]["tools"] == [
        "python-ruff",
        "python-pyrefly",
        "python-bandit",
        "python-radon",
        "shell-shellcheck",
        "shell-shfmt",
        "shell-bats",
        "js-biome",
        "js-typescript-eslint",
        "js-oxlint",
        "security-semgrep",
        "security-osv-scanner",
        "security-gitleaks",
        "js-tsc",
    ]
    assert plan["feedback"]
    assert len(plan["units"]) == 4


def test_build_plan_reports_missing_inference_for_empty_repo(tmp_path: Path) -> None:
    personas, baselines, tools, languages, specialties, strategies = _catalog()
    plan = build_plan(
        target=str(tmp_path),
        personas=personas,
        baselines=baselines,
        tools=tools,
        languages=languages,
        specialties=specialties,
        strategies=strategies,
        config={},
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
        token_policy={
            "profile": "balanced",
            "toon": False,
            "cache_mode": "prompt",
            "model_routing": "right-size",
            "max_parallel_units": 6,
            "max_files_per_unit": 120,
            "max_file_hints": 12,
        },
        strategy_mode="overlay",
    )

    assert plan["selections"]["languages"] == []
    assert plan["selections"]["specialties"] == []
    assert plan["selections"]["tools"] == []
    assert any(item["id"] == "missing-language-pack" for item in plan["feedback_actions"])
    assert any(item["id"] == "missing-specialty-pack" for item in plan["feedback_actions"])
    assert any(item["id"] == "no-deterministic-gates" for item in plan["feedback_actions"])


def test_build_plan_flags_spinner_flows_without_success_confirmation(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "setup_tui.py").write_text("SPINNER_FRAMES = ['⠋', '⠙']\n", encoding="utf-8")

    personas, baselines, tools, languages, specialties, strategies = _catalog()
    plan = build_plan(
        target=str(tmp_path),
        personas=personas,
        baselines=baselines,
        tools=tools,
        languages=languages,
        specialties=specialties,
        strategies=strategies,
        config={"specialties": ["ui-ux-cli-tui"]},
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
        token_policy={
            "profile": "balanced",
            "toon": False,
            "cache_mode": "prompt",
            "model_routing": "right-size",
            "max_parallel_units": 6,
            "max_files_per_unit": 120,
            "max_file_hints": 12,
        },
        strategy_mode="overlay",
    )

    assert any(item["id"] == "ui-ux-spinner-success-state" for item in plan["feedback_actions"])


def test_build_plan_does_not_flag_spinner_flows_with_success_confirmation(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "setup_tui.py").write_text("SPINNER_FRAMES = ['⠋', '⠙']\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("✅ Setup complete. Press Enter to exit.\n", encoding="utf-8")

    personas, baselines, tools, languages, specialties, strategies = _catalog()
    plan = build_plan(
        target=str(tmp_path),
        personas=personas,
        baselines=baselines,
        tools=tools,
        languages=languages,
        specialties=specialties,
        strategies=strategies,
        config={"specialties": ["ui-ux-cli-tui"]},
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
        token_policy={
            "profile": "balanced",
            "toon": False,
            "cache_mode": "prompt",
            "model_routing": "right-size",
            "max_parallel_units": 6,
            "max_files_per_unit": 120,
            "max_file_hints": 12,
        },
        strategy_mode="overlay",
    )

    assert all(item["id"] != "ui-ux-spinner-success-state" for item in plan["feedback_actions"])


def test_build_plan_flags_ui_ux_specialty_without_ux_persona(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "review_tui.py").write_text("", encoding="utf-8")

    personas, baselines, tools, languages, specialties, strategies = _catalog()
    plan = build_plan(
        target=str(tmp_path),
        personas=personas,
        baselines=baselines,
        tools=tools,
        languages=languages,
        specialties=specialties,
        strategies=strategies,
        config={"specialties": ["ui-ux-cli-tui"]},
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
        token_policy={
            "profile": "balanced",
            "toon": False,
            "cache_mode": "prompt",
            "model_routing": "right-size",
            "max_parallel_units": 6,
            "max_files_per_unit": 120,
            "max_file_hints": 12,
        },
        strategy_mode="overlay",
    )

    action = next(item for item in plan["feedback_actions"] if item["id"] == "ui-ux-persona-not-selected")
    assert action["priority"] == "P3"
    assert action["title"] == "Consider enabling the UX reviewer persona"
    assert action["action"].startswith("Add the `ux` persona")


def test_build_plan_does_not_flag_ui_ux_persona_when_selected(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "review_tui.py").write_text("", encoding="utf-8")

    personas, baselines, tools, languages, specialties, strategies = _catalog()
    plan = build_plan(
        target=str(tmp_path),
        personas=personas,
        baselines=baselines,
        tools=tools,
        languages=languages,
        specialties=specialties,
        strategies=strategies,
        config={"personas": ["ux"], "specialties": ["ui-ux-cli-tui"]},
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
        token_policy={
            "profile": "balanced",
            "toon": False,
            "cache_mode": "prompt",
            "model_routing": "right-size",
            "max_parallel_units": 6,
            "max_files_per_unit": 120,
            "max_file_hints": 12,
        },
        strategy_mode="overlay",
    )

    assert all(item["id"] != "ui-ux-persona-not-selected" for item in plan["feedback_actions"])


def test_build_plan_supports_granular_baseline_and_language_practice_selection(tmp_path: Path) -> None:
    personas, baselines, tools, languages, specialties, strategies = _catalog()
    plan = build_plan(
        target=str(tmp_path),
        personas=personas,
        baselines=baselines,
        tools=tools,
        languages=languages,
        specialties=specialties,
        strategies=strategies,
        config={
            "baselines": ["methodology-core"],
            "languages": ["python"],
            "baseline_practices": ["baseline::methodology-core::0"],
            "language_practices": ["language::python::1"],
        },
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
        token_policy={
            "profile": "balanced",
            "toon": False,
            "cache_mode": "prompt",
            "model_routing": "right-size",
            "max_parallel_units": 6,
            "max_files_per_unit": 120,
            "max_file_hints": 12,
        },
        strategy_mode="overlay",
    )

    assert plan["selections"]["baseline_practices"] == ["baseline::methodology-core::0"]
    assert plan["selections"]["language_practices"] == ["language::python::1"]
    assert plan["shared_checks"] == [
        "SOLID/SRP: each module/class/function should have one clear responsibility",
        "Tooling: run Ruff for linting/format checks and treat findings as review input",
    ]


def test_derive_requirements_uses_docs_tests_and_user_input(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "# Acceptance Criteria\n- User can log in\n- System validates token\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_checkout_flow.py").write_text("def test_placeholder():\n    pass\n", encoding="utf-8")

    requirements = derive_requirements(
        target=tmp_path,
        user_requirements=["Payments must reject expired cards"],
    )

    texts = [item["text"] for item in requirements["requirements"]]
    assert any("Payments must reject expired cards" in text for text in texts)
    assert any("User can log in" in text for text in texts)
    assert any("Behavior covered by tests should remain stable for: checkout flow" in text for text in texts)


def test_derive_requirements_reads_general_bullets_without_requirement_heading(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("- Must support retries\n- Must keep audit trail\n", encoding="utf-8")
    requirements = derive_requirements(target=tmp_path)
    texts = [item["text"] for item in requirements["requirements"]]
    assert "Must support retries" in texts
    assert "Must keep audit trail" in texts


def test_detect_issue_provider_auto_variants(tmp_path: Path) -> None:
    assert (
        detect_issue_provider(
            issue_ref="https://dev.azure.com/org/proj/_workitems/edit/123", issue_provider="auto", target=tmp_path
        )
        == "ado"
    )
    assert detect_issue_provider(issue_ref="ABC-123", issue_provider="auto", target=tmp_path) == "jira"
    assert detect_issue_provider(issue_ref="#44", issue_provider="auto", target=tmp_path) == "github"


def test_derive_requirements_records_provider_notes_when_issue_lookup_fails(tmp_path: Path, monkeypatch) -> None:
    def _fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise FileNotFoundError

    monkeypatch.setattr(requirements_module.subprocess, "run", _fake_run)
    requirements = derive_requirements(target=tmp_path, issue_ref="#123", issue_provider="github")
    assert requirements["issue_provider"] == "github"
    assert any("Issue provider resolved to `github`." in note for note in requirements["notes"])


def test_apply_walkthrough_overrides_removes_and_adds_items() -> None:
    baseline = {
        "requirements": [
            {"id": 1, "text": "A", "source": "docs", "confidence": "medium"},
            {"id": 2, "text": "B", "source": "tests", "confidence": "low"},
        ]
    }
    updated = apply_walkthrough_overrides(requirements=baseline, removed_ids={2}, added_items=["C"])
    assert [item["text"] for item in updated["requirements"]] == ["A", "C"]
    assert updated["walkthrough_confirmed"] is True


def test_apply_grilling_refinement_appends_interviewed_requirements() -> None:
    baseline = {
        "requirements": [
            {"id": 1, "text": "Existing", "source": "docs", "confidence": "medium"},
        ],
        "notes": [],
    }
    answers = iter(
        [
            "User can recover from transient API failures",
            "",
            "P95 latency under 300ms",
            "",
            "Add audit trail requirement",
            "",
        ]
    )
    updated = apply_grilling_refinement(requirements=baseline, ask=lambda _prompt: next(answers))
    texts = [item["text"] for item in updated["requirements"]]
    assert "Existing" in texts
    assert "User can recover from transient API failures" in texts
    assert "P95 latency under 300ms" in texts
    assert "Add audit trail requirement" in texts
    assert updated["requirements_refiner"] == "grilling"
    assert any("grilling mode" in note for note in updated["notes"])


def test_to_markdown_renders_requirements_context(tmp_path: Path) -> None:
    personas, baselines, tools, languages, specialties, strategies = _catalog()
    plan = build_plan(
        target=str(tmp_path),
        personas=personas,
        baselines=baselines,
        tools=tools,
        languages=languages,
        specialties=specialties,
        strategies=strategies,
        config={},
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
        token_policy={
            "profile": "balanced",
            "toon": False,
            "cache_mode": "prompt",
            "model_routing": "right-size",
            "max_parallel_units": 6,
            "max_files_per_unit": 120,
            "max_file_hints": 12,
        },
        strategy_mode="overlay",
    )
    plan["requirements_compliance"] = {
        "issue_ref": "#12",
        "issue_provider": "github",
        "walkthrough_confirmed": True,
        "notes": [],
        "requirements": [
            {"id": 1, "text": "User can log in", "source": "issue", "confidence": "high"},
        ],
    }
    rendered = to_markdown(plan)
    assert "## Requirements compliance context" in rendered
    assert "Source issue: `#12`" in rendered
    assert "Issue provider: `github`" in rendered
    assert "(issue/high) User can log in" in rendered
    assert "Action:" in rendered


def test_to_markdown_renders_tool_execution_results(tmp_path: Path) -> None:
    personas, baselines, tools, languages, specialties, strategies = _catalog()
    plan = build_plan(
        target=str(tmp_path),
        personas=personas,
        baselines=baselines,
        tools=tools,
        languages=languages,
        specialties=specialties,
        strategies=strategies,
        config={},
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
        token_policy={
            "profile": "balanced",
            "toon": False,
            "cache_mode": "prompt",
            "model_routing": "right-size",
            "max_parallel_units": 6,
            "max_files_per_unit": 120,
            "max_file_hints": 12,
        },
        strategy_mode="overlay",
    )
    plan["tool_setup_results"] = [
        {
            "id": "python-ruff",
            "status": "failed",
            "steps": [{"kind": "verify", "text": "uvx ruff --version", "status": "failed"}],
        }
    ]
    plan["tool_setup_error"] = "Tool verification failed for python-ruff: uvx ruff --version"
    plan["tool_review_results"] = [
        {
            "id": "python-ruff",
            "status": "passed",
            "steps": [{"kind": "review", "text": "uvx ruff check .", "status": "passed"}],
        }
    ]
    plan["tool_review_error"] = None
    plan["tool_evidence"] = [{"phase": "setup", "id": "python-ruff", "status": "passed", "steps": []}]
    plan["units"][0]["tool_evidence"] = plan["tool_evidence"]

    rendered = to_markdown(plan)
    assert "## Tool execution" in rendered
    assert "python-ruff" in rendered
    assert "Tool verification failed for python-ruff" in rendered
    assert "## Review gate execution" in rendered
    assert "uvx ruff check ." in rendered
    assert "## Shared tool evidence" in rendered
    assert '"axis": "standards|spec"' in rendered
    assert "blocking|important|nit|suggestion|learning|praise" in rendered


def test_build_plan_feedback_actions_are_actionable(tmp_path: Path) -> None:
    personas, baselines, tools, languages, specialties, strategies = _catalog()
    plan = build_plan(
        target=str(tmp_path),
        personas=personas,
        baselines=baselines,
        tools=tools,
        languages=languages,
        specialties=specialties,
        strategies=strategies,
        config={},
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
        token_policy={
            "profile": "balanced",
            "toon": False,
            "cache_mode": "prompt",
            "model_routing": "right-size",
            "max_parallel_units": 6,
            "max_files_per_unit": 120,
            "max_file_hints": 12,
        },
        strategy_mode="overlay",
    )
    assert plan["feedback_actions"]
    first = plan["feedback_actions"][0]
    assert "id" in first
    assert "priority" in first
    assert "action" in first


def test_build_tool_evidence_combines_setup_and_review_results() -> None:
    evidence = build_tool_evidence(
        setup_results=[{"id": "python-ruff", "title": "Ruff", "status": "passed", "steps": []}],
        review_results=[
            {
                "id": "python-ruff",
                "title": "Ruff",
                "status": "passed",
                "steps": [{"kind": "review", "text": "uvx ruff check .", "status": "passed"}],
            }
        ],
    )

    assert evidence[0]["phase"] == "setup"
    assert evidence[1]["phase"] == "review"
    assert evidence[1]["steps"][0]["text"] == "uvx ruff check ."


def test_attach_tool_evidence_to_units_adds_shared_evidence() -> None:
    units = [{"unit_id": "correctness"}, {"unit_id": "security"}]
    evidence = [{"phase": "review", "id": "python-ruff", "status": "passed", "steps": []}]

    updated = attach_tool_evidence_to_units(units=units, tool_evidence=evidence)

    assert updated[0]["tool_evidence"] == evidence
    assert updated[1]["tool_evidence"] == evidence


def test_build_unit_prompt_context_includes_token_and_evidence() -> None:
    unit = {
        "persona_title": "Correctness Reviewer",
        "persona_goal": "Validate behavior against intent and contracts.",
        "token_strategy": {"toon": True, "cache_mode": "context", "model_routing": "right-size"},
        "context_plan": "TOON narrowed: changed-files-first + targeted-hints-only",
        "shared_checks": ["check-1"],
        "checks": ["check-a"],
        "tool_evidence": [{"phase": "review", "id": "python-ruff", "status": "passed"}],
    }

    prompt = build_unit_prompt_context(unit=unit)

    assert "Correctness Reviewer" in prompt
    assert "toon=True" in prompt
    assert "python-ruff" in prompt
    assert "Return only actionable findings with file+line anchors" in prompt


def test_parallel_budget_exceeded_not_emitted_when_profile_budget_not_explicit(tmp_path: Path) -> None:
    personas, baselines, tools, languages, specialties, strategies = _catalog()
    plan = build_plan(
        target=str(tmp_path),
        personas=personas,
        baselines=baselines,
        tools=tools,
        languages=languages,
        specialties=specialties,
        strategies=strategies,
        config={"strategies": ["adversarial", "devils-advocate"]},
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
        token_policy={
            "profile": "balanced",
            "toon": True,
            "cache_mode": "prompt",
            "model_routing": "right-size",
            "max_parallel_units": 6,
            "max_parallel_units_explicit": False,
            "max_files_per_unit": 120,
            "max_file_hints": 12,
        },
        strategy_mode="fanout",
    )
    assert not any(item["id"] == "parallel-budget-exceeded" for item in plan["feedback_actions"])


def test_parallel_budget_exceeded_stays_p1_when_budget_explicit(tmp_path: Path) -> None:
    personas, baselines, tools, languages, specialties, strategies = _catalog()
    plan = build_plan(
        target=str(tmp_path),
        personas=personas,
        baselines=baselines,
        tools=tools,
        languages=languages,
        specialties=specialties,
        strategies=strategies,
        config={"strategies": ["adversarial", "devils-advocate"]},
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
        token_policy={
            "profile": "balanced",
            "toon": True,
            "cache_mode": "prompt",
            "model_routing": "right-size",
            "max_parallel_units": 6,
            "max_parallel_units_explicit": True,
            "max_files_per_unit": 120,
            "max_file_hints": 12,
        },
        strategy_mode="fanout",
    )
    action = next(item for item in plan["feedback_actions"] if item["id"] == "parallel-budget-exceeded")
    assert action["priority"] == "P1"
