---
name: crk
description: Multi-persona subagent code review with selectable baseline packs, language packs, specialty packs, and challenge strategies
version: 1.0.0
license: MIT
---

## Instructions

Use this skill to build a configurable code review panel and run it as parallel subagents.

The review panel is plug-and-play across seven dimensions:
1. **Personas** (quality lenses such as correctness, security, architecture)
2. **Baseline packs** (generic methodology overlays such as SOLID/DRY/KISS/YAGNI/SoC)
3. **Tool packs** (open-source gates such as Ruff, ShellCheck, Biome, Semgrep, OSV-Scanner)
4. **Language packs** (language/framework best practices)
5. **Specialty packs** (domain packs, for example CDK)
6. **Challenge strategies** (adversarial, devil's-advocate, strategic, failure-mode, etc.)
7. **Token strategy** (TOON-style narrowing, right-sized model tiers, and caching policy)

### When to Use

- Before commits and pull requests
- For polyglot repositories
- For domain-specific reviews (cloud, frontend, API, data)
- When you need configurable review depth per change risk
- When you need active challenge strategies to reduce blind spots

### How to Use

#### Prerequisites

- Python 3.12+
- `uv`
- `bash` for setup and verification commands
- Platform package manager for selected tools:
  - macOS: Homebrew
  - Linux/WSL: `apt-get` or your distro equivalent for shell/security tools

Run commands from this repository root. If you are in another repository, run:

```bash
uv run --directory /path/to/code-review-kit crk init --harness copilot --target "$PWD"
```

Install the repo bootstrap files first:

```bash
uv run crk install --harness copilot --target .
```

Then initialize and start the wizard:

```bash
uv run crk init --harness copilot --target .
```

After setup succeeds, `init` can start the review workflow immediately:

```bash
# Ask (default): start now or exit
uv run crk init --harness copilot --target . --post-init-action ask

# Start review automatically after setup
uv run crk init --harness copilot --target . --post-init-action start

# Always exit after setup
uv run crk init --harness copilot --target . --post-init-action exit

# Ask which workflow to run when starting review (dev-loop or pr-review)
uv run crk init --harness copilot --target . --post-init-action ask --post-init-workflow ask

# Start directly in PR review workflow (auto-switches post-review action to PR comments when set to ask)
uv run crk init --harness copilot --target . --post-init-action start --post-init-workflow pr-review

# Provide a specific PR number/URL for PR review workflow
uv run crk init --harness copilot --target . --post-init-action start --post-init-workflow pr-review --pr 123
```

`--pr` accepts numeric IDs and full PR URLs for both GitHub and Azure DevOps.

In GitHub Copilot Chat, start the agent directly:

```text
/agent crk
```

For Copilot harness flows, agent startup now defaults to running the review workflow on the current repo, then ends with two choices: post actionable PR comments (if a PR exists) or generate an implementation plan.

During setup, choose how deterministic tool commands are approved:

```bash
# Prompt before each setup command (persists in state)
uv run crk init --harness copilot --target . --tool-approval prompt

# Auto-allow selected setup commands (default)
uv run crk init --harness copilot --target . --tool-approval allow-selected

# Reset remembered approvals and start clean
uv run crk init --harness copilot --target . --reset-tool-approvals
```

When setup commands run in interactive init, each command now shows live spinner/status feedback immediately instead of waiting for the final summary screen.
The wizard now presents a dedicated tool-approval page immediately after language best-practice selection.
That page explicitly states that allowing a tool also allows crk to install it when missing or inaccessible.
Live braille spinner updates show both installation status (when applicable) and setup/verification status per tool command.

To review cleanup for deselected prior options during re-init:

```bash
uv run crk init --harness copilot --target . --uninstall-deselected-options
```

To apply uninstall commands for deselected tool packs:

```bash
uv run crk init --harness copilot --target . --uninstall-deselected-options --apply-uninstall-deselected-options
```

Generate an orchestration plan directly:

```bash
uv run crk . \
  --personas correctness,security,architecture \
  --baselines methodology-core \
  --tools python-ruff,python-pyrefly,shell-shellcheck,js-biome,security-semgrep \
  --languages typescript \
  --specialties cdk \
  --strategies adversarial,devils-advocate,strategic-critic \
  --strategy-mode fanout \
  --token-profile efficient \
  --cache-mode context \
  --emit markdown
```

Or open the interactive wizard and choose options in a TUI:

```bash
uv run crk . --wizard
```

Or use a profile file:

```bash
uv run crk . --config review-profile.example.json --emit json
```

For review-only requirements compliance, derive and confirm requirements before review planning:

```bash
uv run crk review . \
  --requirements-check \
  --requirements-issue "#123" \
  --issue-provider auto \
  --requirements "Users can reset passwords,Audit logs include actor id" \
  --requirements-walkthrough \
  --requirements-refiner grilling
```

Provider/execution/governance plugin controls:

```bash
uv run crk review . \
  --provider auto \
  --execution-plugin shell-local \
  --execution-fallback-plugin shell-local \
  --sandbox-plugin scratch-home \
  --sandbox-fallback-plugin passthrough \
  --governance-plugin strict-human-approval
```

`--requirements-refiner` modes:
- `manual` (default): standard confirm/remove/add walkthrough.
- `grilling`: one-question-at-a-time requirement refinement before planning.

Persist a repo-specific best-practice learned during review feedback:

```bash
uv run crk review . \
  --learn-best-practice "Use explicit success/error styling in terminal output for better scanability"
```

Update feedback item lifecycle state:

```bash
uv run crk review . \
  --feedback-status "missing-language-pack:in_progress"
```

Promote approved (`accepted`) feedback items into repo learnings:

```bash
uv run crk review . \
  --feedback-status "missing-language-pack:accepted" \
  --learn-accepted-feedback
```

If the user does not choose language or specialty packs, infer likely packs from the target repo before building the plan.
UI/UX specialty inference should trigger for CLI/TUI and HTML/CSS-heavy repositories.
The UI/UX analyzer should also flag spinner/progress flows that do not end with an explicit green checkmark success state.
When UI/UX specialties are selected without the `ux` persona, flag that gap so UX checks run as first-class reviewer units.
References:
- Human-centered design principles: keep feedback timely, use familiar language, preserve user control, and prevent errors.
- Command Line Interface Guidelines (clig.dev): human-first CLI UX, consistency, and composability expectations.
- MDN: `progressbar` role covers long-running progress status, labeling, and value updates.
- MDN: `status` role covers polite live-region updates for advisory information.
- W3C WAI-ARIA APG: `progressbar` pattern covers progress status and indeterminate handling.
If the user does not choose tool packs, infer the most relevant open-source low-setup tools from the selected languages and baseline packs before building the plan.

### Context-quality specialties (do not conflate)

- `harness-context-quality` reviews **LLM/harness-facing context** files.
- `docs-quality` reviews **human-facing documentation** files.
- Keep these separate so reviewers and contributors do not mix prompt quality with documentation quality.
- `docs-quality` best-practice sources:
  - Google Technical Writing: https://developers.google.com/tech-writing
  - Write the Docs beginner guide: https://www.writethedocs.org/guide/writing/beginners-guide-to-docs/

### Harness matrix (aligned to CodeGraph ecosystem)

When implementing or extending `harness-context-quality`, treat these as the supported harness set:

- Claude Code
- Cursor
- Codex CLI
- OpenCode
- Hermes Agent
- Gemini CLI
- Antigravity IDE
- Kiro

Primary context-file patterns: `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`,
`.github/agents/**`, `.github/prompts/**`, `.github/instructions/**`,
`.cursor/rules/**`, `.kiro/steering/**`.

### Agent and skill authoring doctrine

- Keep harness-facing docs compact: activation, workflow contract, harness parity, and one clear next action.
- Use one shared source of truth for activation and workflow text; do not hand-maintain divergent copies.
- Remove TODO placeholders from shipped agent, prompt, and instruction files unless they mark an explicitly scoped follow-up.
- Keep human docs separate from harness docs so README/SKILL stay task-oriented while agents stay executable.
- When review completes, the default next action is either publish a PR comment or continue with an implementation plan.
- Execute plans end-to-end: implement all phases/steps recursively, parallelize only independent work, run sequentially on dependencies, and stop only when requirements are genuinely ambiguous.

Then run the plan:
1. Spawn one subagent per generated review unit.
2. Execute all units in parallel.
3. Collect structured findings from all units.
4. Dedupe findings by file/line/rule and merge duplicate concerns.
5. Prioritize by severity and confidence, then publish one consolidated report.

### Plugin controls

- `--provider auto|github|ado|jira`
- `--execution-plugin <plugin-id>`
- `--execution-fallback-plugin <plugin-id>`
- `--sandbox-plugin <plugin-id>`
- `--sandbox-fallback-plugin <plugin-id>`
- `--governance-plugin strict-human-approval|lenient`

### Schema migration and deprecations

- Config/state payloads are migrated to the current schema version automatically.
- Deprecated keys (for example `reviewer_personas` → `personas`) are mapped during load.
- If deprecated and replacement keys both exist with conflicting values, the command fails fast with a deterministic migration error.

### Token-saving defaults

- **TOON-style narrowing**: prioritize changed files and narrow to targeted hints.
- **Right-sized models**: each review unit gets a recommended model tier.
- **Caching policy**: cache mode is explicit (`none`, `prompt`, `context`, `full`) and part of planning output.
- **Bounded concurrency**: max parallel units are declared in the plan to control token burn.
- **Shared pack checks**: language/specialty checks are centralized once per plan to reduce repeated unit prompts.
- **Tool packs**: tool selection is explicit and defaults are inferred from language/baseline choices, but every pack stays user-selectable.

### Harness notes

#### Harnesses

- `install` writes repo-local registration files under `.github/agents/`, `.github/instructions/`, and `.github/prompts/`.
- `init` performs that bootstrap and starts the interactive wizard when run in a terminal.
- Interactive init shows a progress screen and pauses on success before returning to the shell.
- Post-init startup parity is supported across Copilot, Claude Code, and OpenCode (start-now prompt, workflow selection, and PR prompt flow).
- If the command name collides, rename it during init with `--name`.
- Re-running `init` preloads prior selections from `.code-review/state.json`.
- Deselection cleanup is shown by category (personas/baselines/languages/specialties/tools/strategies), while uninstall execution remains tool-pack-only.
- Review mode can derive requirements from issue/docs/tests and support an interactive confirmation walkthrough.
- Review mode can optionally post actionable PR comments when `--post-review-action comment` is selected and an active PR is available.
- PR comment output follows the Conventional Comments format (`label (decoration): subject`) to clarify intent and blocking status.
- Author-facing PR comments lead with `What failed`, `How urgent`, and `What to do next`, then separate `Tooling status` from `Review findings`.
- Context is collapsed as optional details; provider labels are provider-correct (`Pull request ID` for ADO, `PR #` for GitHub).
- Author-facing PR comments omit local filesystem paths and focus on actionable feedback; full artifacts remain in local CLI/full-plan output.
- Issue lookup is provider-aware with `--issue-provider auto|github|ado|jira` and uses CLI tooling (`gh`, `az`, Jira CLI).
- Sandbox execution defaults to `scratch-home` so deterministic gates run with isolated HOME/XDG state and stay pipeline-safe.

### Wizard controls

- `↑/↓` move
- `space` select or deselect
- `a` select all on current page
- `n` clear all on current page
- `g` toggle all practices in the current methodology/language group (practice pages)
- `[` / `]` jump to previous/next methodology or language group header (practice pages)
- `Enter` advance
- `←/Backspace` go back
- `S` start review from the summary screen

### Feedback loop

The `review` command should write prioritized, actionable feedback (`feedback_actions`) to `.code-review/feedback/latest.json` after each run.
Feedback lifecycle state should persist in `.code-review/feedback/state.json` with an `active_context` subset for token-efficient follow-up.
A learning-approval queue should be written to `.code-review/feedback/learning-queue.json` for governed promotion flow.
Keep init/setup UX clean while preserving an action-oriented SDLC feedback loop.
Use a two-axis review (`standards`, `spec`) and require line-anchored actionable findings with explicit severity taxonomy.
Use `--learn-best-practice` to persist accepted findings into `.code-review/learned-practices.json`; those practices auto-load as the repo-local `repo-learnings` specialty pack on future runs.
Use `--learn-accepted-feedback` (optionally with `--learn-feedback-id`) to promote accepted feedback items and mark them done.

### Review run UX output

Interactive review runs provide explicit lifecycle feedback:
- phase banners at review start and deterministic-gate execution start,
- a dedicated gate-failure summary block when any gate fails,
- a final completion summary with counts, PR action status, and artifact paths.
- PR review workflow can prompt for PR number/URL (or use `--pr`) before publishing comments.
- default output is concise; pass `-vvv` for full detailed plan output.

### Tooling setup

The init flow should run selected tool setup and verification smoke checks, then show a TUI setup summary table with selected tool packs, their prerequisites/setup notes, and verification commands before the user exits.
Full repo lint/type/security/complexity gates still run in review mode, not during init.

## Best-practice workflow

1. Run deterministic pre-checks before subagent review (lint, typecheck, complexity/security scan).
   - For Python projects, use `uvx` with `ruff`, `pyrefly`, and `bandit` for those checks.
   - Add OWASP-style dependency scanning when available in the repo environment.
2. Keep reviewer prompts scoped to changed files when possible.
3. Use strategy overlays or fanout to challenge assumptions, not to inflate noise.
4. Require evidence-based findings with clear remediation guidance.
5. Re-run targeted reviewers after fixes, not the whole panel, unless scope changed.
6. Execute the entire approved plan end-to-end and do not pause after partial slices.

## Examples

**Example: TypeScript + CDK + adversarial panel**

```text
User: Review this CDK TypeScript change using security, architecture, and maintainability personas with adversarial and devil's-advocate strategies.
Agent: Builds a fanout plan (persona x strategy), dispatches subagents in parallel, and returns one consolidated prioritized report.
```

## Limitations

- This skill defines orchestration and defaults; repo-specific policy still belongs in project policy files.
- Output quality depends on persona/pack definitions and selected combinations.
- Unknown IDs fail fast to avoid silent misconfiguration.

## Dependencies

- Python 3.12+
- No external packages (stdlib only)
