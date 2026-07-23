# code-review-kit (crk)

Configurable multi-persona code review skill with selectable baseline methodology packs, tool packs, language packs, specialty packs, and challenge strategies.

Design reference: `DESIGN.md`.

## What this gives you

- Plug-and-play reviewer composition
- Parallel subagent review plan generation
- Language and domain best-practice layering
- Strategy overlays such as adversarial, devil's-advocate, failure-mode, and strategic challenge
- Token optimization with TOON-style narrowing, right-sized model tiers, and cache policy controls

## Quick start

### Prerequisites

- Python 3.12+
- `uv`
- `bash` (used by tool setup and verification commands)
- A platform package manager for selected tools:
  - macOS: Homebrew
  - Linux/WSL: `apt-get` or your distro equivalent for shell/security tools

Run commands from this repository root. If you are in another repository, run:

```bash
uv run --directory /path/to/code-review-kit crk init --harness copilot --target "$PWD"
```

Install the repo bootstrap files:

```bash
uv run crk install --harness copilot --target .
```

Initialize and launch the wizard:

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

Re-run init with cleanup visibility for removed selections:

```bash
uv run crk init --harness copilot --target . --uninstall-deselected-options
```

Apply uninstall commands for deselected tool packs:

```bash
uv run crk init --harness copilot --target . --uninstall-deselected-options --apply-uninstall-deselected-options
```

Or generate a plan directly:

```bash
uv run crk . \
  --personas correctness,security,architecture \
  --baselines methodology-core \
  --tools python-ruff,python-pyrefly,shell-shellcheck,js-biome,security-semgrep \
  --languages typescript \
  --specialties cdk \
  --strategies adversarial,devils-advocate \
  --strategy-mode fanout \
  --token-profile efficient \
  --cache-mode context \
  --emit markdown
```

Generate a plan with requirements-compliance context for review-only flows:

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

`--requirements-refiner` supports:

- `manual` (default): confirm/remove/add requirements interactively.
- `grilling`: one-question-at-a-time refinement to stress-test requirement quality before review.

Capture a new repo-specific best-practice learning from review feedback:

```bash
uv run crk review . \
  --learn-best-practice "Use explicit success/error styling in terminal output for better scanability"
```

Track feedback item lifecycle state:

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

If you skip language or specialty packs, the planner will infer likely packs from the target repo.
UI/UX specialty inference now detects CLI/TUI and HTML/CSS-heavy repositories.
The UI/UX analyzer also flags spinner/progress flows that do not end with an explicit green checkmark success state.
When UI/UX specialties are selected without the `ux` persona, the planner flags that gap so UX checks run as first-class reviewer units.
References:

- Human-centered design principles: keep feedback timely, use familiar language, preserve user control, and prevent errors.
- Command Line Interface Guidelines (clig.dev): human-first CLI UX, consistency, and composability expectations.
- MDN: `progressbar` role covers long-running progress status, labeling, and value updates.
- MDN: `status` role covers polite live-region updates for advisory information.
- W3C WAI-ARIA APG: `progressbar` pattern covers progress status and indeterminate handling.
UI/UX is hierarchical: `ui-ux` is the shared core, while `ui-ux-cli-tui` and `ui-ux-web` are stack-specific overlays users can choose independently.
Baselines are selectable too and can be included/excluded in wizard, CLI, or config.
Tool packs are also selectable; the planner infers a sensible default set from the selected languages/baselines and you can deselect anything you do not want.

## Context-quality specialties (documented contract)

To avoid LLM guesswork when contributors add or modify review logic:

- `harness-context-quality` is for **LLM/harness-facing files only** (agent/prompt/instruction context).
- `docs-quality` is for **human-facing documentation** (README/docs/usage guides).
- These are intentionally separate surfaces and should not be conflated.
- `docs-quality` best-practice sources:
  - Google Technical Writing: <https://developers.google.com/tech-writing>
  - Write the Docs beginner guide: <https://www.writethedocs.org/guide/writing/beginners-guide-to-docs/>

### Harness matrix (aligned to CodeGraph ecosystem)

`harness-context-quality` should treat these as first-class harness targets:

- Claude Code
- Cursor
- Codex CLI
- OpenCode
- Hermes Agent
- Gemini CLI
- Antigravity IDE
- Kiro

Canonical context file patterns include: `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`,
`.github/agents/**`, `.github/prompts/**`, `.github/instructions/**`,
`.cursor/rules/**`, and `.kiro/steering/**`.

## Agent and skill authoring doctrine

- Keep harness-facing docs compact: activation, workflow contract, harness parity, and one clear next action.
- Use one shared source of truth for activation and workflow text; do not hand-maintain divergent copies.
- Remove TODO placeholders from shipped agent, prompt, and instruction files unless they mark an explicitly scoped follow-up.
- Keep human docs separate from harness docs so README/SKILL stay task-oriented while agents stay executable.
- When review completes, the default next action is either publish a PR comment or continue with an implementation plan.
- Execute plans end-to-end: implement all phases/steps recursively, parallelize only independent work, run sequentially on dependencies, and stop only when requirements are genuinely ambiguous.

List built-in catalogs:

```bash
uv run crk . --list-catalog
```

Use a profile file:

```bash
uv run crk . --config review-profile.example.json --emit json
```

## Token strategy controls

- `--token-profile balanced|efficient|ultra`
- `--cache-mode none|prompt|context|full`
- `--model-routing right-size|fixed`
- `--max-parallel-units <N>`

## Plugin controls

- `--provider auto|github|ado|jira`
- `--execution-plugin <plugin-id>`
- `--execution-fallback-plugin <plugin-id>`
- `--sandbox-plugin <plugin-id>`
- `--sandbox-fallback-plugin <plugin-id>`
- `--governance-plugin strict-human-approval|lenient`

## Schema migration and deprecations

- Config/state payloads are migrated to the current schema version automatically.
- Deprecated keys (for example `reviewer_personas` → `personas`) are mapped during load.
- If deprecated and replacement keys both exist with conflicting values, the command fails fast with a deterministic migration error.

TOON narrowing is always enabled.

## Feedback loop

The `review` command writes prioritized, actionable feedback (`feedback_actions`) to `.code-review/feedback/latest.json` after each run.
Feedback lifecycle state is persisted in `.code-review/feedback/state.json` with an `active_context` subset for token-efficient follow-up.
An approval queue artifact is generated at `.code-review/feedback/learning-queue.json` so accepted items can be promoted in a controlled way.
This keeps init/setup UX clean while preserving an action-oriented SDLC feedback loop.
The review workflow is two-axis by default (`standards`, `spec`) and expects line-anchored actionable findings with explicit severity taxonomy.
Use `--learn-best-practice` to persist accepted findings into `.code-review/learned-practices.json`; those practices are auto-loaded as a repo-local specialty pack (`repo-learnings`) on future runs.
Use `--learn-accepted-feedback` (optionally with `--learn-feedback-id`) to promote accepted feedback items and mark them done.
Language/specialty practices are emitted once as shared checks to reduce duplicated per-persona prompts.

## Review run UX output

Interactive review runs now include explicit execution-state UX:

- Phase banners when review starts and when deterministic gates begin.
- A dedicated gate-failure summary block (with failing command) when any setup/review gate fails.
- A final completion block with units/findings/blocking counts, PR action status, and feedback artifact paths.
- PR review workflow can prompt for a PR number/URL (or use `--pr`) before publishing PR comments.
- Default output is concise; use `-vvv` to emit the full multi-section plan details.

## Tooling setup

The init flow runs selected tool setup and verification smoke checks, then shows a TUI setup summary table with each pack's prerequisites/setup notes and verification commands so the user can review what was prepared before exiting.
On Linux and WSL, follow the Linux/WSL branch in the setup notes; Homebrew-only commands are macOS-specific.
Full repo lint/type/security/complexity gates still run in review mode, not during init.

## Mutation tests

Use mutation testing to pressure-check the review and bootstrap logic:

```bash
uvx mutmut run
uvx mutmut browse
```

`pyproject.toml` pins the source and test selection so mutmut focuses on `src/code_review/review_planner/` with the `tests/` suite.

## Python review gates

For Python repositories, run deterministic gates with:

```bash
uvx ruff check
uvx ruff format --check
uvx pyrefly check
uvx bandit -r src -ll
```

For security/dependency sweeps, add OWASP-style dependency checks when available (for example `dependency-check`).

## Tool pack shortlist

- **Python:** Ruff, Pyrefly, Bandit, Radon
- **Shell:** ShellCheck, shfmt, bats-core
- **JS/TS:** Biome, TypeScript compiler, typescript-eslint, oxlint
- **Cross-language:** Semgrep, OSV-Scanner, Gitleaks, detect-secrets, Lizard

## Harness notes

### Harnesses

- `install` writes repo-local registration files under `.github/agents/`, `.github/instructions/`, and `.github/prompts/`.
- `init` performs that bootstrap, runs selected tool setup/verification commands, and then starts the interactive wizard when run in a terminal.
- Interactive init shows a progress screen and pauses on success before returning to the shell.
- Post-init startup parity is supported across Copilot, Claude Code, and OpenCode (start-now prompt, workflow selection, and PR prompt flow).
- If the installed name collides with an existing command, use `crk init --harness <harness> --name <alternate-name>`.
- Rerunning `init` preloads previous selections from `.code-review/state.json`.
- Deselection cleanup output is split by selection areas (personas, baselines, languages, specialties, tools, strategies); uninstall execution is only for deselected tool packs that declare uninstall commands.
- Review mode can derive requirements from issue/docs/tests and run an interactive confirmation walkthrough when `--requirements-check --requirements-walkthrough` are used.
- Review mode can optionally post actionable PR comments when `--post-review-action comment` is selected and an active PR is available.
- PR comment output follows the Conventional Comments format (`label (decoration): subject`) to clarify intent and blocking status.
- Author-facing PR comments lead with `What failed`, `How urgent`, and `What to do next`, then separate `Tooling status` from `Review findings`.
- Context is collapsed as optional details; provider labels are provider-correct (`Pull request ID` for ADO, `PR #` for GitHub).
- Author-facing PR comments omit local filesystem paths and focus on actionable feedback; full artifacts remain in local CLI/full-plan output.
- Issue provider selection supports `--issue-provider auto|github|ado|jira` and resolves via CLI tools (`gh`, `az`, Jira CLI).
- Sandbox execution defaults to `scratch-home` so deterministic gates run with isolated HOME/XDG state and stay pipeline-safe.

## Wizard controls

- `↑/↓` move
- `space` select or deselect
- `a` select all on current page
- `n` clear all on current page
- `g` toggle all practices in the current methodology/language group (practice pages)
- `[` / `]` jump to previous/next methodology or language group header (practice pages)
- `Enter` advance
- `←/Backspace` go back
- `S` start review from the summary screen

## Python version

Python 3.12+

## License

MIT
