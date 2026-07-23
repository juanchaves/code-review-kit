# Agentic Dev Team Integration Plan (ADO-first, Hot-swappable Architecture)

## 1) Objective and execution constraints

This plan integrates agentic-dev-team patterns into `code-review` with:
- Azure DevOps (ADO) as the first-class provider path.
- Plugin/hot-swappable contracts across provider, review workflow, and publishing surfaces.
- Incremental delivery in small, reversible PRs.
- No loss of current CLI behavior for existing users.

Implementation guardrails:
- Deterministic gates execute before reviewer fanout.
- Standards + requirements/spec compliance remain the two review axes.
- Selected tool execution remains mandatory and auditable.
- Completion UX remains constrained to two choices: publish PR comments or generate implementation plan.

---

## 2) Current-state alignment (repo touchpoints)

Primary code surfaces to evolve:
- `src/code_review/cli.py`
  - command-line contract, workflow selection, post-review actions, PR handling.
- `src/code_review/review_planner/planner.py`
  - plan composition, deterministic gate definitions, fanout unit generation, evidence attachment.
- `src/code_review/review_planner/catalog.py`
  - selectable personas/packs/strategies/tool packs and defaults.
- `src/code_review/review_planner/requirements.py`
  - issue-provider detection (`github`/`ado`/`jira`) and requirement derivation.
- `src/code_review/review_planner/init.py`
  - bootstrap/init behavior, setup command policy, execution feedback UX.
- `src/code_review/review_planner/render.py`
  - human-facing markdown output contract.
- `src/code_review/review_planner/doc_contract.py`
  - activation/workflow/harness contract lines.
- `tests/test_planner.py`, `tests/test_init.py`, `tests/test_tui.py`
  - baseline regression and new behavior coverage.

Documentation/harness contract surfaces:
- `README.md`, `SKILL.md`
- `.github/agents/code-review.agent.md`
- `.github/prompts/code-review.prompt.md`
- `.github/instructions/code-review.instructions.md`

---

## 3) Target architecture (ADO-first, plugin/hot-swap)

### 3.1 Plugin seams

Introduce explicit, swappable interfaces (Python protocols or dataclass-backed strategy objects):
1. **IssueProviderPlugin**
   - Resolve issue refs, fetch issue metadata/requirements artifacts.
   - ADO implementation first; GitHub/Jira behind same contract.
2. **PrProviderPlugin**
   - Resolve PR refs and publish line/file/summary comments.
   - ADO implementation first; GitHub adapter parity later.
3. **ReviewExecutionPlugin**
   - Deterministic gate executor and evidence formatter.
   - Default local-shell runner preserved; future remote executor swappable.
4. **GovernancePolicyPlugin**
   - Approval gates for comment publishing / high-risk actions.
   - Human-approval required events normalized across providers.

### 3.2 Architectural rules

- Core orchestration (`cli.py`, `planner.py`) depends on plugin interfaces, never provider specifics.
- Provider-specific parsing/URL logic moves out of generic `requirements.py` flow into provider plugins.
- Plugin registry resolves provider by explicit selection + auto-detection fallback.
- Every plugin call returns typed result envelopes: `ok`, `warnings`, `errors`, `evidence`.

---

## 4) Phased roadmap (Phase 0–4)

## Phase 0 — Baseline contracts and observability scaffolding

### Goals
- Freeze current behavior as executable contracts before structural refactor.
- Add traceable execution/event envelopes for provider and governance decisions.
- Define plugin interface stubs without behavior change.

### In scope
- Add internal plugin interface module(s) and no-op adapters.
- Add schema/version for plan + execution evidence payload.
- Add contract tests for current CLI and workflow invariants.

### Out of scope
- Real ADO PR comment publishing.
- Any default selection changes in personas/packs/tools.

### File-level touchpoints
- `src/code_review/cli.py`
- `src/code_review/review_planner/planner.py`
- `src/code_review/review_planner/render.py`
- `src/code_review/review_planner/doc_contract.py`
- `tests/test_planner.py`, `tests/test_init.py`
- New module(s): `src/code_review/review_planner/plugins/*.py` (or equivalent)

### Rollout strategy
- PR0.1: add interfaces + typed result models (not wired).
- PR0.2: add contract tests capturing current outputs/choices.
- PR0.3: add non-invasive event/evidence metadata fields.

### Risks and mitigations
- **Risk:** hidden behavior drift from new schema fields.
  - **Mitigation:** snapshot tests for markdown/json output stable sections.
- **Risk:** over-design before delivery.
  - **Mitigation:** keep interfaces minimal and usage-driven.

### Measurable acceptance criteria
- 100% existing tests pass unchanged.
- New contract tests assert two completion choices remain enforced.
- Plan/evidence payload includes version field and pass-through metadata.

---

## Phase 1 — ADO-first provider foundation

### Goals
- Implement ADO issue and PR provider plugins as primary path.
- Normalize ADO URL/id parsing and payload mapping under plugin boundary.
- Keep existing GitHub/Jira paths functional via compatibility adapters.

### In scope
- ADO provider plugin for issue requirements retrieval.
- ADO provider plugin for PR metadata and comment target resolution.
- Provider registry + `--issue-provider auto` hardening to prefer ADO when remote/URL indicates ADO.

### Out of scope
- Full GitHub provider refactor.
- Advanced retry/backoff orchestration beyond bounded local retries.

### File-level touchpoints
- `src/code_review/review_planner/requirements.py`
- `src/code_review/cli.py`
- `src/code_review/review_planner/planner.py` (provider metadata in plan)
- `tests/test_planner.py`, `tests/test_init.py`
- New tests: `tests/test_requirements_provider_ado.py`
- New module(s): `src/code_review/review_planner/providers/ado.py`, `.../registry.py`

### Rollout strategy
- PR1.1: provider registry + adapter wrappers around existing functions.
- PR1.2: ADO issue plugin adoption behind feature flag/env toggle.
- PR1.3: ADO PR metadata/comment-target resolution with fallback to legacy path.

### Risks and mitigations
- **Risk:** ADO CLI/API variance by environment.
  - **Mitigation:** strict error normalization and actionable warnings; fixture-based tests for payload variants.
- **Risk:** regression in non-ADO users.
  - **Mitigation:** compatibility adapters, legacy path retained until Phase 4 exit.

### Measurable acceptance criteria
- `auto` provider detection resolves ADO correctly for ADO remotes and URLs.
- Issue requirements derivation returns normalized requirements for ADO sample payloads.
- Non-ADO flows remain green in existing test suite.

---

## Phase 2 — Workflow orchestration + governance gates

### Goals
- Wire provider plugins into end-to-end review/post-review workflow.
- Enforce explicit governance approvals before external side effects (PR publishing).
- Preserve deterministic-gates-first ordering.

### In scope
- Workflow state machine in CLI path: `review -> summarize -> human choice -> provider publish|plan`.
- Governance policy plugin requiring explicit human approval signal for publish.
- Structured audit trail in output/evidence for decision path.
- Author-facing PR comment contract using Conventional Comments (`label (decoration): subject`) with explicit blocking/non-blocking intent.
- Provider-aware PR context labels (avoid ambiguous `#<id>` rendering that can be interpreted as non-PR artifacts in ADO).

### Out of scope
- Multi-approver enterprise workflow.
- Organization-level RBAC management.

### File-level touchpoints
- `src/code_review/cli.py`
- `src/code_review/review_planner/planner.py`
- `src/code_review/review_planner/render.py`
- `src/code_review/review_planner/init.py`
- `tests/test_planner.py`, `tests/test_tui.py`
- New tests: `tests/test_governance_policy.py`, `tests/test_pr_publish_flow.py`

### Rollout strategy
- PR2.1: introduce governance decision model and wire no-op deny-by-default policy.
- PR2.2: enforce approval gate before publish action.
- PR2.3: add audit rendering and regression tests for blocked publish path.

### Risks and mitigations
- **Risk:** accidental auto-publish.
  - **Mitigation:** deny-by-default policy; explicit approval token/flag required.
- **Risk:** UX friction from extra gate.
  - **Mitigation:** concise prompts, clear rationale, direct remediation path.

### Measurable acceptance criteria
- Publishing path blocked when approval signal absent.
- Approved publish path emits provider call evidence and success/failure details.
- Completion choices remain exactly two in user-facing flow.
- PR comments are action-oriented and omit local filesystem artifacts (for example absolute target/report paths).
- Tooling execution failures are separated from review findings in the PR comment body.
- Comment items are machine-parseable Conventional Comments entries.

---

## Phase 3 — Hot-swappable provider and execution plugins (production hardening)

### Goals
- Make provider + execution plugin selection explicit and runtime-swappable.
- Enable independent plugin testing and failure isolation.
- Add graceful degradation and fallback semantics.

### In scope
- Plugin registry supports explicit `--provider`/config selection and capability discovery.
- Execution plugin wrappers for deterministic gates and review gate evidence formatting.
- Fallback policy: selected plugin failure falls back only where policy allows.

### Out of scope
- Dynamic plugin installation from external registries.
- Arbitrary untrusted plugin loading.

### File-level touchpoints
- `src/code_review/review_planner/catalog.py` (provider/tool metadata exposure)
- `src/code_review/review_planner/planner.py`
- `src/code_review/cli.py`
- `src/code_review/review_planner/render.py`
- `tests/test_planner.py`, `tests/test_init.py`
- New tests: `tests/test_plugin_registry.py`, `tests/test_execution_plugin.py`

### Rollout strategy
- PR3.1: plugin capability model + registry listing in catalog output.
- PR3.2: CLI flags/config for explicit plugin selection.
- PR3.3: fallback semantics + failure contracts + docs updates.

### Risks and mitigations
- **Risk:** config complexity explosion.
  - **Mitigation:** conservative defaults (`auto`, ADO-first), validate unknown plugin IDs early.
- **Risk:** hidden partial execution.
  - **Mitigation:** mandatory explicit failure when selected required plugin did not run.

### Measurable acceptance criteria
- Plugin selection visible in plan output.
- Unknown plugin ID fails fast with actionable message.
- Required-plugin skip/failure is surfaced as explicit review failure.

---

## Phase 4 — Migration, compatibility exit, and operational readiness

### Goals
- Migrate existing users safely to plugin-backed architecture.
- Remove deprecated legacy provider paths after compatibility window.
- Finalize operational runbook and dogfood loop.

### In scope
- State/config schema migration with versioning.
- Backward-compatibility shim for prior config keys/behavior.
- Deprecation warnings, timeline, and final removals.

### Out of scope
- New feature expansions not required for migration safety.

### File-level touchpoints
- `src/code_review/cli.py`
- `src/code_review/review_planner/init.py`
- `src/code_review/review_planner/io_utils.py`
- `README.md`, `SKILL.md`
- `.github/agents/code-review.agent.md`, `.github/prompts/code-review.prompt.md`
- `tests/test_init.py`, `tests/test_planner.py`, `tests/test_tui.py`
- New tests: `tests/test_config_migration.py`

### Rollout strategy
- PR4.1: schema migration helpers + compatibility warnings.
- PR4.2: docs/runbook updates and migration command examples.
- PR4.3: remove deprecated branches guarded by migration success metrics.

### Risks and mitigations
- **Risk:** silent config breakage for existing users.
  - **Mitigation:** auto-migrate with dry-run preview and explicit diff summary.
- **Risk:** deprecation fatigue.
  - **Mitigation:** phased warnings (info -> warning -> error), with dates/releases.

### Measurable acceptance criteria
- Legacy configs load with migration warning and equivalent effective behavior.
- Migration tests cover old->new transformations and rollback fallback.
- Dogfood loop confirms end-to-end ADO-first flow on real PRs.

---

## 5) BDD/ATDD contract scenarios by phase

## Phase 0 scenarios

### Happy path
- **Given** current repo and existing CLI args
- **When** the planner builds a review plan
- **Then** output choices and review axes remain unchanged, with added non-breaking schema version metadata.

### Edge/failure path
- **Given** malformed plugin metadata in config
- **When** plan construction starts
- **Then** CLI fails fast with actionable validation error and no partial workflow execution.

### Governance path (human approval)
- **Given** a request to publish PR comments
- **When** approval state is undefined
- **Then** action is blocked and explicit human-approval requirement is shown.

## Phase 1 scenarios

### Happy path
- **Given** an ADO remote URL and valid work item id
- **When** requirements derivation runs with `--issue-provider auto`
- **Then** ADO provider plugin is selected and normalized requirement items are returned.

### Edge/failure path
- **Given** ADO CLI unavailable
- **When** ADO provider is selected
- **Then** workflow continues with clear warning, marks issue-derived requirements skipped, and preserves user-provided requirements.

### Governance path (human approval)
- **Given** provider auto-resolution proposes non-default provider override
- **When** runtime is configured in strict-governance mode
- **Then** human confirmation is required before executing provider-specific network actions.

## Phase 2 scenarios

### Happy path
- **Given** deterministic gates passed and approval granted
- **When** user chooses publish comments
- **Then** provider publish executes and evidence trail records gate results, approval decision, and publish outcome.

### Edge/failure path
- **Given** deterministic gate failure on required tool
- **When** post-review publish is requested
- **Then** publish is blocked, failure is explicit, and remediation points to failing gate evidence.

### Governance path (human approval)
- **Given** review findings include blocking severity items
- **When** publish-to-PR is attempted
- **Then** explicit human approval prompt is mandatory before side-effecting comment publish.

## Phase 3 scenarios

### Happy path
- **Given** provider plugin explicitly set to `ado`
- **When** review runs end-to-end
- **Then** registry resolves `ado`, execution plugin runs required gates, and plan/output include plugin capability summary.

### Edge/failure path
- **Given** selected plugin ID is unknown
- **When** command starts
- **Then** process fails before review fanout with list of valid plugin IDs.

### Governance path (human approval)
- **Given** runtime fallback from primary to secondary plugin is possible
- **When** fallback would cross provider boundary
- **Then** governance policy requires human approval before fallback execution.

## Phase 4 scenarios

### Happy path
- **Given** a legacy config/state file
- **When** init/review runs on new version
- **Then** auto-migration applies, behavior remains equivalent, and migration summary is logged.

### Edge/failure path
- **Given** legacy config contains deprecated keys with conflicting values
- **When** migration executes
- **Then** command exits with deterministic mapping error and suggested corrected keys.

### Governance path (human approval)
- **Given** migration would remove deprecated publish-related behavior
- **When** user is in compatibility grace window
- **Then** human confirmation is required before irreversible migration cleanup.

---

## 6) Parallelization strategy (concurrent vs sequential)

## Must remain sequential
1. Load config/state and resolve plugin/provider.
2. Validate selection contracts.
3. Run deterministic gates for required tools.
4. Build and attach tool evidence.
5. Run reviewer fanout.
6. Aggregate/dedupe findings.
7. Human decision gate (publish vs plan).
8. Provider publish side effect (if approved).

## Can run concurrently
- Per-tool deterministic gate commands within a phase when independent.
- Persona/strategy review units (`max_parallel_units` bounded).
- Non-side-effect metadata fetches (issue enrichment, static docs read) after provider resolution.
- Post-fanout finding normalization by independent category buckets before final merge.

## Dependency matrix
- Provider resolution -> required before issue/PR integration calls.
- Deterministic gates -> required before publish eligibility.
- Governance decision -> required before any external write.
- Migration transform -> required before reading legacy config/state.

## Small-PR parallel workstreams
- Stream A: provider/plugin interfaces + tests.
- Stream B: governance model + CLI wiring.
- Stream C: render/output schema updates.
- Stream D: migration utilities and docs.

Only merge streams with explicit integration checkpoints after each phase.

---

## 7) Migration plan and backward compatibility

## User migration steps
1. Introduce schema version field in state/config outputs.
2. On read, detect legacy schema and run pure transformation to latest shape.
3. Emit concise migration summary (old keys -> new keys).
4. Continue execution with transformed in-memory config.
5. Offer optional write-back command/path to persist migrated config.

## Compatibility strategy
- Keep existing CLI flags functional; map deprecated flags to new plugin-aware options.
- Preserve default behavior:
  - `--issue-provider auto` remains default.
  - Existing personas/packs/tool defaults unchanged unless explicitly configured.
- Keep legacy provider code path behind compatibility adapter until Phase 4 exit criteria met.
- Add deprecation stages:
  - Stage 1: informational notice
  - Stage 2: warning + migration hint
  - Stage 3: hard error with explicit remediation

## Exit criteria for legacy path removal
- 2 consecutive releases with zero blocker migration defects.
- Dogfood + CI passing for ADO-first default flows.
- Documented migration completion rate and rollback readiness.

---

## 8) Test strategy mapping (unit / integration / e2e / manual dogfood)

| Test level | Primary scope | Phase mapping | Example touchpoints | Expected evidence |
|---|---|---|---|---|
| Unit | Plugin contracts, provider parsing, migration transforms, governance policy decisions | 0-4 | `tests/test_plugin_registry.py`, `tests/test_requirements_provider_ado.py`, `tests/test_config_migration.py` | Deterministic function-level pass/fail, edge-case fixtures |
| Integration | CLI -> planner -> provider registry -> render composition | 1-4 | `tests/test_planner.py`, `tests/test_init.py`, new publish-flow tests | End-to-end payload correctness, explicit blocked/approved publish behavior |
| E2E (local scripted) | Real command workflows on fixture repos and sample configs | 2-4 | `uv run code-review init ...`, `uv run code-review review ...` | Full workflow transcripts with tool evidence and decision logs |
| Manual dogfood loop | Human-in-the-loop approval and PR publish in real ADO projects | 2-4 | ADO-connected repo using default workflow | Verified usability, governance clarity, publish correctness |

## Validation command mapping (incremental)
- Fast unit loop per PR: `uv run pytest -q tests/test_planner.py tests/test_init.py`
- Provider/governance additions: targeted new test modules only.
- Pre-merge per phase: `uv run pytest -q tests`

---

## 9) Incremental PR plan (executable)

- **PR-00x (Phase 0):** plugin interfaces + schema version + baseline contracts.
- **PR-01x (Phase 1):** ADO provider registry integration + adapter path.
- **PR-02x (Phase 2):** governance gate + publish workflow wiring.
- **PR-03x (Phase 3):** explicit plugin selection and fallback policy.
- **PR-04x (Phase 4):** migration tooling, compatibility cleanup, docs.

Each PR must include:
- focused scope (single phase step),
- targeted tests for changed behavior,
- explicit rollback notes in PR description,
- no unrelated refactors.

---

## 10) Definition of done (overall)

Integration is complete when:
1. ADO-first provider path is default and production-viable.
2. Provider/execution/governance seams are hot-swappable via stable contracts.
3. Deterministic-gates-first and two-axis review contracts remain intact.
4. Publish side effects are governed by explicit human approval.
5. Existing users migrate without behavior loss through compatibility window.
6. Unit/integration/e2e/manual dogfood evidence is green for all phases.
