from __future__ import annotations

from pathlib import Path

from .catalog import DEFAULT_BASELINES, DEFAULT_PERSONAS, DEFAULT_TOOLS, Pack, Persona, Strategy, ToolPack
from .token_strategy import choose_model_tier, toon_trim_hints

BRAILLE_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
SUCCESS_EMOJI = "✅"
IGNORED_SCAN_DIRS = {
    ".git",
    ".venv",
    ".code-review",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "__pycache__",
}


def parse_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _is_ignored_path(path: Path, target: Path) -> bool:
    try:
        relative = path.relative_to(target)
    except ValueError:
        relative = path
    return any(part in IGNORED_SCAN_DIRS for part in relative.parts)


def parse_practice_ids(raw: object, *, key_name: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError(f"'{key_name}' must be a string array in config.")
    return ordered_unique(raw)


def derive_pack_ids_from_practices(*, selected_ids: list[str], expected_kind: str) -> list[str]:
    pack_ids: list[str] = []
    for item in selected_ids:
        parts = item.split("::", 2)
        if len(parts) != 3:
            continue
        item_kind, pack_id, index_raw = parts
        if item_kind != expected_kind or not index_raw.isdigit():
            continue
        if pack_id not in pack_ids:
            pack_ids.append(pack_id)
    return pack_ids


def select_ids(
    *,
    kind: str,
    catalog: dict[str, object],
    default: list[str],
    config: dict,
    cli_values: list[str],
    cli_excludes: list[str],
) -> list[str]:
    include = list(default)
    config_include = config.get(kind, [])
    config_exclude = config.get(f"exclude_{kind}", [])

    if config_include:
        if not isinstance(config_include, list) or not all(isinstance(item, str) for item in config_include):
            raise ValueError(f"'{kind}' must be a string array in config.")
        include = config_include

    if config_exclude and (
        not isinstance(config_exclude, list) or not all(isinstance(item, str) for item in config_exclude)
    ):
        raise ValueError(f"'exclude_{kind}' must be a string array in config.")

    include = ordered_unique(include + cli_values)
    excludes = set(config_exclude + cli_excludes)

    unknown = sorted({*include, *excludes} - set(catalog))
    if unknown:
        available = ", ".join(sorted(catalog))
        raise ValueError(f"Unknown {kind}: {', '.join(unknown)}. Available: {available}")

    selected = [item for item in include if item not in excludes]
    if not selected and kind == "personas":
        raise ValueError("No personas selected after exclusions.")
    return selected


def merged_hints(
    persona: Persona, baseline_packs: list[Pack], language_packs: list[Pack], specialty_packs: list[Pack]
) -> list[str]:
    hints = (
        persona.file_hints
        + [h for pack in baseline_packs for h in pack.file_hints]
        + [h for pack in language_packs for h in pack.file_hints]
        + [h for pack in specialty_packs for h in pack.file_hints]
    )
    return ordered_unique(hints)


def _repo_text_files(target: Path) -> list[Path]:
    if not target.exists() or not target.is_dir():
        return []
    text_suffixes = {
        ".py",
        ".md",
        ".txt",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".sh",
        ".yml",
        ".yaml",
        ".json",
        ".toml",
        ".ini",
        ".cfg",
    }
    names = {"README", "SKILL", "CLAUDE", "AGENTS", "GEMINI"}
    files: list[Path] = []
    for path in target.rglob("*"):
        if not path.is_file():
            continue
        if _is_ignored_path(path, target):
            continue
        if path.suffix.lower() in text_suffixes or path.stem in names:
            files.append(path)
    return files


def _repo_contains_braille_spinner(target: Path) -> bool:
    for path in _repo_text_files(target):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(frame in text for frame in BRAILLE_SPINNER_FRAMES):
            return True
    return False


def _repo_contains_success_emoji(target: Path) -> bool:
    for path in _repo_text_files(target):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if SUCCESS_EMOJI in text:
            return True
    return False


def _authoring_doc_paths(target: Path) -> list[Path]:
    if not target.exists() or not target.is_dir():
        return []
    paths: list[Path] = []
    for path in target.rglob("*.md"):
        if _is_ignored_path(path, target):
            continue
        rel = path.relative_to(target).as_posix()
        if (rel.startswith((".github/agents/", ".github/prompts/", ".github/instructions/"))) or path.name in {
            "README.md",
            "SKILL.md",
            "CLAUDE.md",
            "AGENTS.md",
            "GEMINI.md",
        }:
            paths.append(path)
    return paths


def _read_authoring_text(paths: list[Path]) -> str:
    chunks: list[str] = []
    for path in paths:
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return "\n".join(chunks)


def infer_language_ids(target: Path, languages: dict[str, Pack]) -> list[str]:
    if not target.exists() or not target.is_dir():
        return []

    extension_map = {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".mjs": "javascript",
        ".sh": "shell",
        ".bash": "shell",
        ".zsh": "shell",
    }
    counts: dict[str, int] = {}
    for path in target.rglob("*"):
        if not path.is_file():
            continue
        if _is_ignored_path(path, target):
            continue
        pack_id = extension_map.get(path.suffix)
        if pack_id and pack_id in languages:
            counts[pack_id] = counts.get(pack_id, 0) + 1
    return [item[0] for item in sorted(counts.items(), key=lambda entry: (-entry[1], entry[0]))]


def infer_specialty_ids(target: Path, specialties: dict[str, Pack]) -> list[str]:
    if not target.exists() or not target.is_dir():
        return []

    candidates: list[str] = []
    paths = [path.relative_to(target).as_posix() for path in target.rglob("*")]
    if "cdk.json" in {path.name for path in target.rglob("*")} or any(
        "cdk/" in item or item.endswith("/cdk") for item in paths
    ):
        candidates.append("cdk")
    if any(path.suffix == ".tf" for path in target.rglob("*.tf") if not _is_ignored_path(path, target)):
        candidates.append("terraform")
    if any(
        item.startswith(("k8s/", "helm/"))
        for item in paths
        if not any(part in IGNORED_SCAN_DIRS for part in Path(item).parts)
    ):
        candidates.append("kubernetes")
    if any(
        item.startswith("api/") or "openapi/" in item
        for item in paths
        if not any(part in IGNORED_SCAN_DIRS for part in Path(item).parts)
    ):
        candidates.append("api")
    if any(
        path.suffix in {".tsx", ".jsx"}
        for path in target.rglob("*")
        if path.is_file() and not _is_ignored_path(path, target)
    ):
        candidates.append("react")
    if any(
        path.suffix == ".vue" for path in target.rglob("*") if path.is_file() and not _is_ignored_path(path, target)
    ):
        candidates.append("vue")
    cli_tui_detected = any(
        "tui" in path.stem.lower() or "cli" in path.stem.lower()
        for path in target.rglob("*")
        if path.is_file() and not _is_ignored_path(path, target)
    )
    web_ui_detected = any(
        path.suffix in {".html", ".css", ".scss", ".sass", ".less"}
        for path in target.rglob("*")
        if path.is_file() and not _is_ignored_path(path, target)
    )
    if cli_tui_detected:
        candidates.append("ui-ux-cli-tui")
    if web_ui_detected:
        candidates.append("ui-ux-web")
    if cli_tui_detected or web_ui_detected:
        candidates.append("ui-ux")

    harness_files = {
        ".github/copilot-instructions.md",
        "CLAUDE.md",
        "AGENTS.md",
        "GEMINI.md",
    }
    if any(item in harness_files for item in paths) or any(
        item.startswith(
            (
                ".github/prompts/",
                ".github/instructions/",
                ".github/agents/",
                ".cursor/rules/",
                ".kiro/steering/",
            )
        )
        for item in paths
    ):
        candidates.append("harness-context-quality")

    markdown_files = [path for path in target.rglob("*.md") if path.is_file() and not _is_ignored_path(path, target)]
    if (target / "docs").is_dir() or len(markdown_files) >= 3:
        candidates.append("docs-quality")
    return [item for item in ordered_unique(candidates) if item in specialties]


def expand_specialty_hierarchy(selected_ids: list[str]) -> list[str]:
    expanded: list[str] = []
    child_to_parent = {
        "ui-ux-cli-tui": "ui-ux",
        "ui-ux-web": "ui-ux",
    }
    for item in selected_ids:
        parent = child_to_parent.get(item)
        if parent and parent not in expanded:
            expanded.append(parent)
        if item not in expanded:
            expanded.append(item)
    return expanded


def infer_tool_ids(
    *,
    selected_baselines: list[str],
    selected_languages: list[str],
    selected_specialties: list[str],
    tools: dict[str, ToolPack],
) -> list[str]:
    candidates: list[str] = []

    if "python" in selected_languages:
        candidates.extend(["python-ruff", "python-pyrefly", "python-bandit", "python-radon"])
    if "shell" in selected_languages:
        candidates.extend(["shell-shellcheck", "shell-shfmt", "shell-bats"])
    if "javascript" in selected_languages:
        candidates.extend(
            [
                "js-biome",
                "js-typescript-eslint",
                "js-oxlint",
                "security-semgrep",
                "security-osv-scanner",
                "security-gitleaks",
            ]
        )
    if "typescript" in selected_languages:
        candidates.extend(
            [
                "js-tsc",
                "js-biome",
                "js-typescript-eslint",
                "js-oxlint",
                "security-semgrep",
                "security-osv-scanner",
                "security-gitleaks",
            ]
        )

    if "review-quality-gates" in selected_baselines:
        candidates.extend(
            [
                "security-semgrep",
                "security-gitleaks",
                "security-detect-secrets",
                "security-osv-scanner",
                "complexity-lizard",
            ]
        )
    if "code-smells-refactoring" in selected_baselines:
        if "python" in selected_languages:
            candidates.append("python-radon")
        candidates.append("complexity-lizard")

    if "security" in selected_specialties:
        candidates.extend(["security-semgrep", "security-gitleaks", "security-detect-secrets", "security-osv-scanner"])
    if "api" in selected_specialties:
        candidates.extend(["security-semgrep", "security-osv-scanner"])
    if "cdk" in selected_specialties:
        candidates.extend(["security-semgrep", "security-osv-scanner", "security-gitleaks"])

    return [item for item in ordered_unique(candidates) if item in tools]


def shared_checks(
    baseline_packs: list[tuple[str, Pack]],
    language_packs: list[tuple[str, Pack]],
    specialty_packs: list[tuple[str, Pack]],
    baseline_practice_ids: list[str] | None = None,
    language_practice_ids: list[str] | None = None,
) -> list[str]:
    baseline_selected = selected_practices_for_packs(
        pack_type="baseline",
        packs=baseline_packs,
        selected_ids=baseline_practice_ids or [],
    )
    language_selected = selected_practices_for_packs(
        pack_type="language",
        packs=language_packs,
        selected_ids=language_practice_ids or [],
    )
    checks = baseline_selected + language_selected + [p for _, pack in specialty_packs for p in pack.practices]
    return ordered_unique(checks)


def selected_practices_for_packs(
    *, pack_type: str, packs: list[tuple[str, Pack]], selected_ids: list[str]
) -> list[str]:
    if not selected_ids:
        return [practice for _, pack in packs for practice in pack.practices]

    selections_by_pack: dict[str, set[int]] = {}
    for item in selected_ids:
        parts = item.split("::", 2)
        if len(parts) != 3:
            continue
        item_pack_type, pack_id, index_raw = parts
        if item_pack_type != pack_type:
            continue
        if not index_raw.isdigit():
            continue
        selections_by_pack.setdefault(pack_id, set()).add(int(index_raw))

    selected: list[str] = []
    for pack_id, pack in packs:
        selected_indexes = selections_by_pack.get(pack_id)
        if selected_indexes is None:
            selected.extend(pack.practices)
            continue
        for index, practice in enumerate(pack.practices):
            if index in selected_indexes:
                selected.append(practice)
    return selected


def deterministic_gates_for_selection(selected_tool_ids: list[str], tools: dict[str, ToolPack]) -> list[dict]:
    gates: list[dict] = []
    for tool_id in selected_tool_ids:
        tool = tools[tool_id]
        gates.append(
            {
                "id": tool_id,
                "title": tool.title,
                "category": ",".join(tool.applies_to),
                "why": tool.purpose,
                "setup": tool.setup,
                "commands": tool.commands,
                "review_commands": tool.review_commands,
            }
        )
    return gates


def build_tool_evidence(*, setup_results: list[dict], review_results: list[dict]) -> list[dict]:
    evidence: list[dict] = []

    for phase, results in (("setup", setup_results), ("review", review_results)):
        for result in results:
            if not isinstance(result, dict):
                continue
            steps: list[dict] = []
            for step in result.get("steps", []):
                if not isinstance(step, dict):
                    continue
                step_payload = {
                    "kind": step.get("kind", phase),
                    "text": step.get("text", ""),
                    "status": step.get("status", "passed"),
                }
                if step.get("stderr"):
                    step_payload["stderr"] = step["stderr"]
                if step.get("stdout"):
                    step_payload["stdout"] = step["stdout"]
                steps.append(step_payload)
            evidence.append(
                {
                    "phase": phase,
                    "id": result.get("id", ""),
                    "title": result.get("title", ""),
                    "status": result.get("status", ""),
                    "steps": steps,
                }
            )

    return evidence


def build_unit_prompt_context(*, unit: dict) -> str:
    tool_lines: list[str] = []
    for item in unit.get("tool_evidence", []):
        if not isinstance(item, dict):
            continue
        tool_lines.append(f"- {item.get('phase', '')}:{item.get('id', '')}:{item.get('status', '')}")

    strategy = unit.get("token_strategy", {})
    shared_checks = unit.get("shared_checks", [])
    checks = unit.get("checks", [])
    return "\n".join(
        [
            f"Persona: {unit.get('persona_title', '')}",
            f"Goal: {unit.get('persona_goal', '')}",
            f"Token strategy: toon={strategy.get('toon')}, cache={strategy.get('cache_mode')}, routing={strategy.get('model_routing')}",
            f"Context plan: {unit.get('context_plan', '')}",
            f"Shared checks: {len(shared_checks)}",
            f"Persona checks: {len(checks)}",
            "Tool evidence:",
            *(tool_lines or ["- none"]),
            "Use two axes: standards-compliance and spec/requirements-compliance.",
            "Return only actionable findings with file+line anchors, severity (blocking|important|nit|suggestion|learning|praise), confidence, issue, evidence citation, and recommendation.",
        ]
    )


def attach_tool_evidence_to_units(*, units: list[dict], tool_evidence: list[dict]) -> list[dict]:
    for unit in units:
        if isinstance(unit, dict):
            unit["tool_evidence"] = tool_evidence
    return units


def build_units(
    *,
    personas: dict[str, Persona],
    strategies: dict[str, Strategy],
    selected_personas: list[str],
    selected_strategies: list[str],
    strategy_mode: str,
    selected_baselines: list[Pack],
    selected_languages: list[Pack],
    selected_specialties: list[Pack],
    shared_pack_checks: list[str],
    tool_evidence: list[dict],
    token_policy: dict,
) -> list[dict]:
    units: list[dict] = []
    overlay_directives = [
        directive for strategy_id in selected_strategies for directive in strategies[strategy_id].directives
    ]

    for persona_id in selected_personas:
        persona = personas[persona_id]
        checks = ordered_unique(persona.checks)
        hints = merged_hints(persona, selected_baselines, selected_languages, selected_specialties)
        if token_policy["toon"]:
            hints = toon_trim_hints(hints, token_policy["max_file_hints"])

        if strategy_mode == "fanout" and selected_strategies:
            for strategy_id in selected_strategies:
                unit_strategy_ids = [strategy_id]
                units.append(
                    {
                        "unit_id": f"{persona_id}__{strategy_id}",
                        "persona_id": persona_id,
                        "persona_title": persona.title,
                        "persona_goal": persona.goal,
                        "strategy_ids": unit_strategy_ids,
                        "strategy_directives": strategies[strategy_id].directives,
                        "checks": checks,
                        "shared_checks": shared_pack_checks,
                        "tool_evidence": tool_evidence,
                        "token_strategy": token_policy,
                        "file_hints": hints,
                        "recommended_model_tier": choose_model_tier(
                            persona_id, unit_strategy_ids, token_policy["model_routing"]
                        ),
                        "cache_key": f"{persona_id}|{strategy_id}|{token_policy['cache_mode']}",
                        "context_plan": "TOON narrowed: changed-files-first + targeted-hints-only"
                        if token_policy["toon"]
                        else "Standard: full hint set",
                    }
                )
                units[-1]["prompt_context"] = build_unit_prompt_context(unit=units[-1])
            continue

        unit_strategy_ids = selected_strategies
        unit = {
            "unit_id": persona_id,
            "persona_id": persona_id,
            "persona_title": persona.title,
            "persona_goal": persona.goal,
            "strategy_ids": unit_strategy_ids,
            "strategy_directives": ordered_unique(overlay_directives),
            "checks": checks,
            "shared_checks": shared_pack_checks,
            "tool_evidence": tool_evidence,
            "token_strategy": token_policy,
            "file_hints": hints,
            "recommended_model_tier": choose_model_tier(persona_id, unit_strategy_ids, token_policy["model_routing"]),
            "cache_key": f"{persona_id}|overlay|{token_policy['cache_mode']}",
            "context_plan": "TOON narrowed: changed-files-first + targeted-hints-only"
            if token_policy["toon"]
            else "Standard: full hint set",
        }
        unit["prompt_context"] = build_unit_prompt_context(unit=unit)
        units.append(unit)
    return units


def build_plan(
    *,
    target: str,
    personas: dict[str, Persona],
    baselines: dict[str, Pack],
    tools: dict[str, ToolPack],
    languages: dict[str, Pack],
    specialties: dict[str, Pack],
    strategies: dict[str, Strategy],
    config: dict,
    cli_inputs: dict,
    token_policy: dict,
    strategy_mode: str,
) -> dict:
    selected_personas = select_ids(
        kind="personas",
        catalog=personas,
        default=DEFAULT_PERSONAS,
        config=config,
        cli_values=cli_inputs["personas"],
        cli_excludes=cli_inputs["exclude_personas"],
    )
    selected_languages_ids = select_ids(
        kind="languages",
        catalog=languages,
        default=[],
        config=config,
        cli_values=cli_inputs["languages"],
        cli_excludes=cli_inputs["exclude_languages"],
    )
    selected_specialties_ids = select_ids(
        kind="specialties",
        catalog=specialties,
        default=[],
        config=config,
        cli_values=cli_inputs["specialties"],
        cli_excludes=cli_inputs["exclude_specialties"],
    )
    selected_strategies_ids = select_ids(
        kind="strategies",
        catalog=strategies,
        default=[],
        config=config,
        cli_values=cli_inputs["strategies"],
        cli_excludes=cli_inputs["exclude_strategies"],
    )
    selected_baselines_ids = select_ids(
        kind="baselines",
        catalog=baselines,
        default=DEFAULT_BASELINES,
        config=config,
        cli_values=cli_inputs["baselines"],
        cli_excludes=cli_inputs["exclude_baselines"],
    )
    baseline_practice_ids = parse_practice_ids(config.get("baseline_practices", []), key_name="baseline_practices")
    language_practice_ids = parse_practice_ids(config.get("language_practices", []), key_name="language_practices")
    if not selected_baselines_ids and baseline_practice_ids:
        selected_baselines_ids = derive_pack_ids_from_practices(
            selected_ids=baseline_practice_ids,
            expected_kind="baseline",
        )

    target_path = Path(target)
    if not selected_languages_ids:
        selected_languages_ids = infer_language_ids(target_path, languages)
    if not selected_languages_ids and language_practice_ids:
        selected_languages_ids = derive_pack_ids_from_practices(
            selected_ids=language_practice_ids,
            expected_kind="language",
        )
    if not selected_specialties_ids:
        selected_specialties_ids = infer_specialty_ids(target_path, specialties)
    selected_specialties_ids = expand_specialty_hierarchy(selected_specialties_ids)
    selected_tools_ids = select_ids(
        kind="tools",
        catalog=tools,
        default=DEFAULT_TOOLS
        or infer_tool_ids(
            selected_baselines=selected_baselines_ids,
            selected_languages=selected_languages_ids,
            selected_specialties=selected_specialties_ids,
            tools=tools,
        ),
        config=config,
        cli_values=cli_inputs["tools"],
        cli_excludes=cli_inputs["exclude_tools"],
    )

    selected_baseline_packs = [baselines[item] for item in selected_baselines_ids]
    selected_language_packs = [languages[item] for item in selected_languages_ids]
    selected_specialty_packs = [specialties[item] for item in selected_specialties_ids]
    baseline_pack_items = [(item, baselines[item]) for item in selected_baselines_ids]
    language_pack_items = [(item, languages[item]) for item in selected_languages_ids]
    specialty_pack_items = [(item, specialties[item]) for item in selected_specialties_ids]
    shared_pack_checks = shared_checks(
        baseline_pack_items,
        language_pack_items,
        specialty_pack_items,
        baseline_practice_ids=baseline_practice_ids,
        language_practice_ids=language_practice_ids,
    )
    deterministic_gates = deterministic_gates_for_selection(selected_tools_ids, tools)

    units = build_units(
        personas=personas,
        strategies=strategies,
        selected_personas=selected_personas,
        selected_strategies=selected_strategies_ids,
        strategy_mode=strategy_mode,
        selected_baselines=selected_baseline_packs,
        selected_languages=selected_language_packs,
        selected_specialties=selected_specialty_packs,
        shared_pack_checks=shared_pack_checks,
        tool_evidence=[],
        token_policy=token_policy,
    )
    shared_check_overlaps = sum(1 for unit in units for check in unit["checks"] if check in shared_pack_checks)

    feedback_actions = build_feedback_actions(
        target_path=target_path,
        personas=selected_personas,
        baselines=selected_baselines_ids,
        tools=selected_tools_ids,
        languages=selected_languages_ids,
        specialties=selected_specialties_ids,
        strategies=selected_strategies_ids,
        token_policy=token_policy,
        unit_count=len(units),
        has_shared_pack_checks=bool(shared_pack_checks),
        shared_check_overlaps=shared_check_overlaps,
        has_deterministic_gates=bool(deterministic_gates),
    )

    return {
        "target": target,
        "selections": {
            "personas": selected_personas,
            "baselines": selected_baselines_ids,
            "baseline_practices": baseline_practice_ids,
            "tools": selected_tools_ids,
            "languages": selected_languages_ids,
            "language_practices": language_practice_ids,
            "specialties": selected_specialties_ids,
            "strategies": selected_strategies_ids,
            "strategy_mode": strategy_mode,
        },
        "token_strategy": token_policy,
        "shared_checks": shared_pack_checks,
        "deterministic_gates": deterministic_gates,
        "feedback_actions": feedback_actions,
        "feedback": build_feedback(
            selected_personas,
            selected_baselines_ids,
            selected_tools_ids,
            selected_languages_ids,
            selected_specialties_ids,
            selected_strategies_ids,
            target_path,
            token_policy,
            len(units),
            bool(shared_pack_checks),
            shared_check_overlaps,
            bool(deterministic_gates),
        ),
        "units": units,
    }


def build_feedback(
    personas: list[str],
    baselines: list[str],
    tools: list[str],
    languages: list[str],
    specialties: list[str],
    strategies: list[str],
    target_path: Path,
    token_policy: dict,
    unit_count: int,
    has_shared_pack_checks: bool,
    shared_check_overlaps: int,
    has_deterministic_gates: bool,
) -> list[str]:
    feedback: list[str] = []
    for action in build_feedback_actions(
        personas=personas,
        baselines=baselines,
        tools=tools,
        languages=languages,
        specialties=specialties,
        strategies=strategies,
        target_path=target_path,
        token_policy=token_policy,
        unit_count=unit_count,
        has_shared_pack_checks=has_shared_pack_checks,
        shared_check_overlaps=shared_check_overlaps,
        has_deterministic_gates=has_deterministic_gates,
    ):
        feedback.append(f"[{action['priority']}] {action['title']} — Action: {action['action']}")
    return feedback


def build_feedback_actions(
    *,
    personas: list[str],
    baselines: list[str],
    tools: list[str],
    languages: list[str],
    specialties: list[str],
    strategies: list[str],
    target_path: Path,
    token_policy: dict,
    unit_count: int,
    has_shared_pack_checks: bool,
    shared_check_overlaps: int,
    has_deterministic_gates: bool,
) -> list[dict]:
    actions: list[dict] = []

    if not baselines:
        actions.append(
            {
                "id": "missing-baseline-pack",
                "priority": "P1",
                "title": "Missing baseline methodology coverage",
                "action": "Select at least one baseline pack (for example: methodology-core).",
                "why": "Core methodology checks (SOLID/DRY/KISS/YAGNI/SoC) are currently missing.",
            }
        )
    if not tools:
        actions.append(
            {
                "id": "missing-tool-gates",
                "priority": "P1",
                "title": "Missing deterministic tooling gates",
                "action": "Select tool packs for lint/type/security/complexity checks before subagent fanout.",
                "why": "No tooling pack is selected, so the review lacks deterministic pre-check signal.",
            }
        )
    if not languages:
        actions.append(
            {
                "id": "missing-language-pack",
                "priority": "P2",
                "title": "Missing language-specific best-practice coverage",
                "action": "Select or infer language packs before review planning.",
                "why": "Without language packs, review checks stay generic and may miss idiomatic risks.",
            }
        )
    if not specialties:
        actions.append(
            {
                "id": "missing-specialty-pack",
                "priority": "P2",
                "title": "Missing domain-specific coverage",
                "action": "Select or infer at least one specialty pack aligned to the repository surface.",
                "why": "No specialty pack was selected or inferred, so domain-specific guidance is absent.",
            }
        )
    budget_is_explicit = bool(token_policy.get("max_parallel_units_explicit", False))
    if budget_is_explicit and unit_count > token_policy["max_parallel_units"]:
        actions.append(
            {
                "id": "parallel-budget-exceeded",
                "priority": "P1",
                "title": "Parallel unit budget exceeded",
                "action": f"Reduce strategy fanout or increase max parallel units above {token_policy['max_parallel_units']}.",
                "why": f"The plan created {unit_count} units, which exceeds the configured parallel budget.",
            }
        )
    if token_policy["toon"] and token_policy["max_file_hints"] < 10:
        actions.append(
            {
                "id": "toon-hint-budget-low",
                "priority": "P2",
                "title": "TOON hint budget may be too narrow",
                "action": "Increase max file hints or verify changed-files coverage before executing the panel.",
                "why": "A small hint budget can hide relevant files from reviewers.",
            }
        )
    if strategies and token_policy["model_routing"] == "fixed":
        actions.append(
            {
                "id": "fixed-routing-with-strategies",
                "priority": "P3",
                "title": "Fixed routing with challenge strategies",
                "action": "Switch to right-size model routing for strategy-heavy runs.",
                "why": "Challenge strategies may need stronger model tiers than fixed routing provides.",
            }
        )
    if has_shared_pack_checks and shared_check_overlaps > 0:
        actions.append(
            {
                "id": "shared-checks-centralized",
                "priority": "P3",
                "title": "Shared checks are centralized",
                "action": "Remove overlapping checks from persona-specific lists and keep them only in shared checks.",
                "why": f"Detected {shared_check_overlaps} overlaps between unit checks and shared checks.",
            }
        )
    if (
        "ui-ux-cli-tui" in specialties
        and _repo_contains_braille_spinner(target_path)
        and not _repo_contains_success_emoji(target_path)
    ):
        actions.append(
            {
                "id": "ui-ux-spinner-success-state",
                "priority": "P2",
                "title": "Animated setup flows lack an explicit success state",
                "action": "End braille/spinner-style setup flows with a visible green checkmark success message before exit.",
                "why": "The repo uses animated progress affordances, but no success emoji was found to confirm completion.",
            }
        )
    if (
        "ui-ux" in specialties or "ui-ux-cli-tui" in specialties or "ui-ux-web" in specialties
    ) and "ux" not in personas:
        actions.append(
            {
                "id": "ui-ux-persona-not-selected",
                "priority": "P3",
                "title": "Consider enabling the UX reviewer persona",
                "action": "Add the `ux` persona to evaluate comment readability and action hierarchy in this run.",
                "why": "UI/UX specialties are selected without the dedicated UX reviewer persona.",
            }
        )
    authoring_paths = _authoring_doc_paths(target_path)
    authoring_text = _read_authoring_text(authoring_paths)
    if authoring_paths and "harness-context-quality" in specialties and len(authoring_text.split()) > 900:
        actions.append(
            {
                "id": "harness-docs-too-large",
                "priority": "P3",
                "title": "Harness-facing docs are carrying too much context",
                "action": "Trim repeated guidance and keep only the activation contract, workflow steps, and harness caveats in harness-facing files.",
                "why": "Harness docs should stay compact so they remain high-signal and token-efficient.",
            }
        )
    if not has_deterministic_gates:
        actions.append(
            {
                "id": "no-deterministic-gates",
                "priority": "P1",
                "title": "No deterministic gates configured",
                "action": "Add static-analysis/security commands in tool packs before relying on reviewer findings.",
                "why": "Subagent-only review without deterministic gates increases missed-defect risk.",
            }
        )
    if not actions:
        actions.append(
            {
                "id": "feedback-loop-next-step",
                "priority": "P3",
                "title": "Feedback loop next step is ready",
                "action": "Run one review cycle and capture at least one actionable gap to feed back into pack or workflow config.",
                "why": "The plan shape is healthy; next improvement should come from real-run evidence.",
            }
        )
    return actions
