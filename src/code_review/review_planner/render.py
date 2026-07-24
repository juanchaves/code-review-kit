from __future__ import annotations

from .catalog import Pack, Persona, Strategy, ToolPack
from .init import platform_label
from .plugins.execution import build_default_execution_registry
from .plugins.governance import build_default_governance_registry
from .plugins.providers import build_default_provider_registry
from .plugins.sandbox import build_default_sandbox_registry
from .token_strategy import TOKEN_PROFILES


def render_tool_setup_results(results: list[dict]) -> str:
    lines = [
        "## Tool setup execution",
        "",
        f"Platform: {platform_label()}",
        "",
    ]
    if not results:
        lines.append("No selected tool packs required setup.")
        return "\n".join(lines) + "\n"

    for result in results:
        lines.append(f"- **{result['id']}**: {result['status']}")
        for step in result.get("steps", []):
            status = step.get("status", "passed")
            prefix = (
                "setup"
                if step.get("kind") == "setup"
                else "verify"
                if step.get("kind") == "verify"
                else "review"
                if step.get("kind") == "review"
                else "prereq"
            )
            lines.append(f"  - {prefix}: `{step.get('text', '')}` ({status})")
    return "\n".join(lines) + "\n"


def to_markdown(plan: dict) -> str:
    token_policy = plan["token_strategy"]
    feedback_actions = plan.get("feedback_actions", [])
    feedback_lines: list[str] = []
    if isinstance(feedback_actions, list) and feedback_actions:
        for item in feedback_actions:
            if not isinstance(item, dict):
                continue
            priority = item.get("priority", "P3")
            title = item.get("title", "Feedback item")
            action = item.get("action", "")
            why = item.get("why", "")
            feedback_lines.append(f"- [{priority}] {title}")
            if action:
                feedback_lines.append(f"  - Action: {action}")
            if why:
                feedback_lines.append(f"  - Why: {why}")
    else:
        feedback_lines = [f"- {item}" for item in plan.get("feedback", [])]

    lines = [
        "# Multi-persona review plan",
        "",
        f"- Target: `{plan['target']}`",
        f"- Review scope mode: `{plan.get('review_scope', {}).get('mode', 'repo-current')}`",
        f"- Review scope base ref: `{plan.get('review_scope', {}).get('base_ref') or 'none'}`",
        f"- Review axes: `{', '.join(plan.get('review_axes', ['standards', 'spec']))}`",
        f"- Schema version: `{plan.get('schema_version', 'n/a')}`",
        f"- Provider preference: `{plan.get('provider_preference', 'auto')}`",
        f"- Execution plugin: `{plan.get('execution_plugin', 'shell-local')}`",
        f"- Sandbox plugin: `{plan.get('sandbox_plugin', 'scratch-home')}`",
        f"- Governance plugin: `{plan.get('governance', {}).get('plugin', 'strict-human-approval') if isinstance(plan.get('governance'), dict) else 'strict-human-approval'}`",
        f"- Personas: `{', '.join(plan['selections']['personas'])}`",
        f"- Baselines: `{', '.join(plan['selections']['baselines']) or 'none'}`",
        f"- Baseline practices: `{len(plan['selections'].get('baseline_practices', []))}`",
        f"- Tools: `{', '.join(plan['selections']['tools']) or 'none'}`",
        f"- Languages: `{', '.join(plan['selections']['languages']) or 'none'}`",
        f"- Language practices: `{len(plan['selections'].get('language_practices', []))}`",
        f"- Specialties: `{', '.join(plan['selections']['specialties']) or 'none'}`",
        f"- Strategies: `{', '.join(plan['selections']['strategies']) or 'none'}`",
        f"- Strategy mode: `{plan['selections']['strategy_mode']}`",
        "",
        "## Token optimization strategy",
        "",
        f"- Token profile: `{token_policy['profile']}`",
        f"- TOON narrowing: `{'enabled' if token_policy['toon'] else 'disabled'}`",
        f"- Model routing: `{token_policy['model_routing']}`",
        f"- Cache mode: `{token_policy['cache_mode']}`",
        f"- Max parallel units: `{token_policy['max_parallel_units']}`",
        f"- Max files per unit (guidance): `{token_policy['max_files_per_unit']}`",
        "",
        "## Feedback actions",
        "",
        *feedback_lines,
        "",
        "## Shared baseline/language/specialty checks",
        "",
        *([f"- {item}" for item in plan.get("shared_checks", [])] or ["- none"]),
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
    lines.append("")
    tool_setup_results = plan.get("tool_setup_results", [])
    if isinstance(tool_setup_results, list):
        lines.append("## Tool execution")
        lines.append("")
        lines.extend(render_tool_setup_results(tool_setup_results).rstrip("\n").splitlines())
        lines.append("")
        tool_setup_error = plan.get("tool_setup_error")
        if tool_setup_error:
            lines.append(f"Error: {tool_setup_error}")
            lines.append("")
    tool_review_results = plan.get("tool_review_results", [])
    if isinstance(tool_review_results, list):
        lines.append("## Review gate execution")
        lines.append("")
        for result in tool_review_results:
            lines.append(f"- **{result.get('id', '')}**: {result.get('status', '')}")
            for step in result.get("steps", []):
                if not isinstance(step, dict):
                    continue
                lines.append(f"  - review: `{step.get('text', '')}` ({step.get('status', '')})")
        lines.append("")
        tool_review_error = plan.get("tool_review_error")
        if tool_review_error:
            lines.append(f"Error: {tool_review_error}")
            lines.append("")
    tool_evidence = plan.get("tool_evidence", [])
    if isinstance(tool_evidence, list) and tool_evidence:
        lines.append("## Shared tool evidence")
        lines.append("")
        for item in tool_evidence:
            if not isinstance(item, dict):
                continue
            lines.append(f"- **{item.get('phase', '')}** `{item.get('id', '')}`: {item.get('status', '')}")
        lines.append("")
    lines.append("## Review units")

    for unit in plan["units"]:
        lines.extend(
            [
                "",
                f"### {unit['unit_id']}",
                f"- Persona: **{unit['persona_title']}**",
                f"- Goal: {unit['persona_goal']}",
                f"- Strategies: `{', '.join(unit['strategy_ids']) or 'none'}`",
                f"- Recommended model tier: `{unit['recommended_model_tier']}`",
                f"- Cache key: `{unit['cache_key']}`",
                f"- Context plan: {unit['context_plan']}",
                f"- Token strategy: `toon={'enabled' if unit.get('token_strategy', {}).get('toon') else 'disabled'}`, cache={unit.get('token_strategy', {}).get('cache_mode')}, routing={unit.get('token_strategy', {}).get('model_routing')}",
                f"- Tool evidence items: `{len(unit.get('tool_evidence', []))}`",
                f"- Prompt context: `{unit.get('prompt_context', '')[:120]}{'...' if len(unit.get('prompt_context', '')) > 120 else ''}`",
                "- File hints:",
                *[f"  - `{hint}`" for hint in unit["file_hints"]],
                "- Persona checks:",
                *[f"  - {check}" for check in unit["checks"]],
                "- Shared checks source: `plan.shared_checks`",
                "- Strategy directives:",
                *([f"  - {directive}" for directive in unit["strategy_directives"]] or ["  - none"]),
            ]
        )

    lines.extend(
        [
            "",
            "## Execution guidance",
            "",
            "1. Run tool availability checks first during init; run full deterministic repo gates during review.",
            "2. Preload cache for shared language/specialty context blocks.",
            "3. Spawn one subagent per review unit, bounded by max parallel units.",
            "4. Enforce changed-files-first reads (TOON), then use targeted hint globs only.",
            "5. Require evidence-based findings with severity and confidence.",
            "6. Dedupe overlaps across units and merge duplicate concerns.",
            "7. Re-run only impacted units after fixes.",
            "",
            "## Finding contract",
            "",
            "Each unit should return JSON findings in this shape:",
            "```json",
            "{",
            '  "reviewer": "<unit_id>",',
            '  "axis": "standards|spec",',
            '  "status": "pass|warn|fail",',
            '  "findings": [',
            "    {",
            '      "file": "<path>",',
            '      "line": 1,',
            '      "severity": "blocking|important|nit|suggestion|learning|praise",',
            '      "confidence": "high|medium|low",',
            '      "category": "<category>",',
            '      "issue": "<what is wrong>",',
            '      "evidence": "<code citation>",',
            '      "recommendation": "<how to fix>"',
            "    }",
            "  ]",
            "}",
            "```",
            "",
            "Rules: avoid unanchored findings, dedupe repeated concerns, and keep comments concise and actionable.",
        ]
    )

    requirements_context = plan.get("requirements_compliance")
    if isinstance(requirements_context, dict):
        lines.extend(["", "## Requirements compliance context", ""])
        issue_ref = requirements_context.get("issue_ref")
        issue_provider = requirements_context.get("issue_provider")
        lines.append(f"- Source issue: `{issue_ref}`" if issue_ref else "- Source issue: `none`")
        lines.append(f"- Issue provider: `{issue_provider}`" if issue_provider else "- Issue provider: `none`")
        lines.append(
            f"- Walkthrough confirmed: `{'yes' if requirements_context.get('walkthrough_confirmed') else 'no'}`"
        )
        notes = requirements_context.get("notes", [])
        if notes:
            lines.append("- Notes:")
            lines.extend([f"  - {note}" for note in notes])
        requirements = requirements_context.get("requirements", [])
        lines.append("- Derived requirements:")
        if requirements:
            for item in requirements:
                lines.append(f"  - [{item['id']}] ({item['source']}/{item['confidence']}) {item['text']}")
        else:
            lines.append("  - none")
    migration_notes = plan.get("migration_notes", [])
    if isinstance(migration_notes, list) and migration_notes:
        lines.extend(["", "## Migration notes", ""])
        lines.extend([f"- {note}" for note in migration_notes if isinstance(note, str) and note.strip()])
    return "\n".join(lines) + "\n"


def to_catalog_markdown(
    personas: dict[str, Persona],
    baselines: dict[str, Pack],
    tools: dict[str, ToolPack],
    languages: dict[str, Pack],
    specialties: dict[str, Pack],
    strategies: dict[str, Strategy],
) -> str:
    lines = ["# Catalogs", "", "## Personas"]
    lines.extend([f"- `{key}`: {value.title}" for key, value in sorted(personas.items())])
    lines.append("")
    lines.append("## Baseline packs")
    lines.extend([f"- `{key}`: {value.title}" for key, value in sorted(baselines.items())])
    lines.append("")
    lines.append("## Tool packs")
    lines.extend([f"- `{key}`: {value.title}" for key, value in sorted(tools.items())])
    lines.append("")
    lines.append("## Language packs")
    lines.extend([f"- `{key}`: {value.title}" for key, value in sorted(languages.items())])
    lines.append("")
    lines.append("## Specialty packs")
    lines.extend([f"- `{key}`: {value.title}" for key, value in sorted(specialties.items())])
    lines.append("")
    lines.append("## Strategies")
    lines.extend([f"- `{key}`: {value.title}" for key, value in sorted(strategies.items())])
    lines.append("")
    lines.append("## Token profiles")
    lines.extend([f"- `{key}`" for key in sorted(TOKEN_PROFILES)])
    lines.append("")
    lines.append("## Provider plugins")
    provider_registry = build_default_provider_registry()
    lines.extend([f"- issue:`{key}`" for key in sorted(provider_registry.issue_providers)])
    lines.extend([f"- pr:`{key}`" for key in sorted(provider_registry.pr_providers)])
    lines.append("")
    lines.append("## Execution plugins")
    execution_registry = build_default_execution_registry(
        run_selected_tool_setup=lambda **_kwargs: ([], None),
        run_deterministic_gates=lambda **_kwargs: ([], None),
    )
    lines.extend([f"- `{key}`" for key in sorted(execution_registry.plugins)])
    lines.append("")
    lines.append("## Sandbox plugins")
    sandbox_registry = build_default_sandbox_registry()
    lines.extend([f"- `{key}`" for key in sorted(sandbox_registry.plugins)])
    lines.append("")
    lines.append("## Governance plugins")
    governance_registry = build_default_governance_registry()
    lines.extend([f"- `{key}`" for key in sorted(governance_registry.plugins)])
    lines.append("")
    return "\n".join(lines)
