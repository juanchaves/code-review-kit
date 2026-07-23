# code-review Design

## Goals

- Keep the system modular and configurable.
- Make core capabilities hot-swappable without degrading UX.
- Prioritize Azure DevOps (ADO) as the primary provider now, while supporting GitHub parity where practical.

## Architecture Principles

1. Plugin-first boundaries for all major surfaces:
   - SCM/PR providers (ADO, GitHub, later Jira-linked workflows and other systems)
   - Analysis engines/tools (linters, security scanners, complexity tools, etc.)
   - Requirements refiners (manual walkthrough, grilling-style refinement, future provider-aware refiners)
   - Best-practice packs (baseline, language, specialty, strategy overlays)
2. Stable contracts between core orchestration and plugins:
   - Capability discovery (what each plugin can do)
   - Standardized request/response models
   - Explicit error semantics
3. Runtime configurability:
   - Users can select and swap providers/tools/packs via config and wizard flows.
   - Default recommendations are allowed; hard mandates are avoided unless safety-critical.
4. UX consistency across swaps:
   - Same workflow shape, status messaging, and confirmation semantics regardless of chosen provider/tooling plugin.
   - No provider-specific UX regressions when switching integrations.

## Workflow Modes

### 1. PR Review Workflow (`pr-review`)

Purpose: review a PR and publish actionable feedback to the PR system.

- Inputs: provider, repo/project context, PR id/url, optional scope overrides.
- Execution:
  - Fetch/sync PR branch or diff scope.
  - Run selected deterministic gates and multi-persona analysis on scoped changes.
  - Route findings to the provider in the most precise location:
    - line-level comments/suggestions when anchored to changed LOC
    - file/module comments for grouped concerns
    - summary comment for cross-cutting/system-level findings
- Output: provider-native review comments + consolidated summary.

### 2. Developer Feedback Loop Workflow (`dev-loop`)

Purpose: iterative local feedback during active development.

- Inputs: local repo/worktree context, selected packs/tools/strategies.
- Execution:
  - Fast scoped checks and persona feedback cycles.
  - Re-run only impacted units/gates as code changes.
  - Persist feedback state for iterative closure and learning promotion.
- Output: local actionable plan/findings optimized for rapid iteration.

## Provider Strategy

- **Current priority:** ADO-first implementation and UX hardening.
- **Secondary:** GitHub provider support in parallel when it does not slow ADO delivery.
- **Future:** additional providers (including Jira-adjacent workflows) through the same plugin contract.

## UX Invariants (Non-negotiable)

- Immediate acknowledgement when starting long-running phases.
- Visible progress/status throughout execution.
- Explicit completion state with next actions.
- Consistent interaction model across provider/tool/plugin swaps.
