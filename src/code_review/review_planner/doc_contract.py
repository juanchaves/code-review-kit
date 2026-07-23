from __future__ import annotations


def activation_message(name: str) -> str:
    return f"Ready — starting {name} workflow for the current repository now."


def workflow_contract_lines(name: str) -> list[str]:
    return [
        "## Activation",
        "",
        "When this agent is selected without a prompt, the very first assistant message MUST be:",
        "",
        f'"{activation_message(name)}"',
        "",
        "Then execute this workflow automatically for Copilot/GitHub Copilot harnesses:",
        "1. Assume the current repository is the default target.",
        "2. Run deterministic tool gates before reviewer fanout.",
        "3. Run two review axes: standards compliance and spec/requirements compliance.",
        "4. Report only actionable findings with file+line anchors, severity, confidence, and fix guidance.",
        "5. Do not claim tool execution unless selected tools were actually run.",
        "6. When posting PR comments, format feedback using Conventional Comments labels (`issue`, `suggestion`, `note`, etc.) with explicit blocking/non-blocking decorations.",
        "7. Keep PR comments author-facing: lead with what failed/how urgent/what to do next, avoid local filesystem paths, and separate tooling status from review findings.",
        "8. Use provider-correct context labels (`Pull request ID` for ADO, `PR #` for GitHub) and keep optional context collapsed.",
        "9. End by asking the user to choose one next action:",
        "   - Post actionable comments to the active PR (if a PR exists), or",
        "   - Generate an implementation plan from the findings.",
        "",
        "If the current repository cannot be determined, ask exactly one concise follow-up question for the target.",
    ]


def harness_parity_lines() -> list[str]:
    return [
        "## Harness startup parity",
        "",
        "- Copilot, Claude Code, and OpenCode should all follow the same post-init startup flow:",
        "  1. Ask whether to start the review workflow now (unless `--post-init-action` overrides).",
        "  2. Allow workflow selection (`dev-loop` or `pr-review`) when `--post-init-workflow ask` is used.",
        "  3. Prompt for PR number/URL in PR workflow when no `--pr` argument was provided.",
    ]


def workflow_instructions_lines(name: str) -> list[str]:
    return [
        f"# {name} review workflow",
        "",
        "- Keep personas, baseline packs, tool packs, language packs, specialty packs, and strategies selectable.",
        "- Keep prompt context compact: changed-files-first scope, selected checks, and tool evidence only.",
        "- Prefer deterministic gates before parallel subagent review.",
        "- Use two review axes: standards compliance and spec/requirements compliance.",
        "- Favor evidence-based findings with file+line references and concrete remediation.",
        "- Use Conventional Comments format for PR feedback (`label (decoration): subject`).",
        "- Keep PR comments concise and author-facing; omit local machine paths from comment bodies.",
        "- Lead PR comments with failure/urgency/next-step summary and split tooling status from review findings.",
        "- Use provider-correct labels and keep extended context optional/collapsed.",
        f"- For harness runs started via `/agent {name}` (or equivalent), auto-start review on the current repository.",
        "- Treat selected-tool execution as mandatory: if required tools were skipped, surface that as an explicit failure.",
        "- After review, provide exactly two completion choices: post actionable PR comments (when PR exists) or generate an implementation plan.",
        "- Keep startup and post-review behavior consistent across Copilot, Claude Code, and OpenCode.",
        "- Execute approved plans end-to-end: complete all dependent phases and steps before stopping unless requirements are ambiguous.",
    ]


def prompt_contract_lines(name: str) -> list[str]:
    return [
        f"Start immediately: run the {name} workflow on the current repository.",
        "",
        "Workflow contract:",
        "1. Run selected deterministic tools first and report exact execution status.",
        "2. Execute both axes: standards and spec/requirements.",
        "3. Return only actionable, line-anchored findings with severity + confidence.",
        "4. End with two options: post PR comments (if PR exists) or generate implementation plan.",
        "",
        "If repository context is unavailable, ask one concise question for the target path/branch/diff and continue.",
    ]
