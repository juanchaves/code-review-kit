# Spec: Init Module Boundary Cleanup

> **Amendment (during `/plan`, before implementation)**: the Architecture Specification and
> Acceptance Criteria below were corrected after the initial consistency-gate PASS to permit
> two legitimate, pre-existing one-directional dependencies discovered while planning:
> (1) `init.py`'s `promote_accepted_feedback_to_learnings` calling `record_learned_practices`,
> and (2) the relocated `render_tool_setup_results` needing Init's platform-detection helper.
> The original constraint text ("no new import in either direction") was too strict; the
> corrected text is what appears below. See `plans/init-module-boundary-cleanup.md` → Risks
> & Open Questions for the full account.

## Intent Description

`review_planner/init.py` implements the harness-bootstrap lifecycle (install/init/uninstall
bootstrap files, OS/WSL detection, tool-setup execution) but has also accreted two unrelated
responsibilities: a pure Markdown-formatting function (`render_tool_setup_results`) that
`render.py` depends on, and learned-practices persistence/merging (`record_learned_practices`,
`merge_learned_extensions`) that operates directly on the Catalog vocabulary (personas,
baselines, tools, languages, specialties, strategies) and is consumed by the `review` command
flow. This creates semantic coupling: Render and the review value stream depend on Init for
functionality that isn't Init's concern, blurring context boundaries and making `init.py`
harder to reason about. Relocating these two responsibilities to where they conceptually
belong removes the coupling and makes `init.py` legible as "harness bootstrap only."

## Architecture Specification

**Components affected:**

- `src/code_review/review_planner/init.py` — remove `render_tool_setup_results`,
  `record_learned_practices`, `merge_learned_extensions`, and their private
  load/save/path helpers.
- `src/code_review/review_planner/render.py` — gains `render_tool_setup_results` as a
  locally defined function; remove its import of that function from `init.py`.
  `render_tool_setup_results` calls `_platform_label()` (init.py:131-136), a private
  OS/harness-detection helper also used elsewhere in `init.py` (line 923) for genuinely
  Init-owned logic — this one must NOT move or be duplicated. Rename it to a public
  `platform_label()` in `init.py` and have `render.py` import that one function. This is
  a one-directional Render → Init dependency on a capability Init legitimately owns
  (platform detection), which is the correct direction — distinct from the
  rendering-format function this spec relocates out of Init.
- New module `src/code_review/review_planner/learning.py` — gains
  `record_learned_practices`, `merge_learned_extensions`, and their supporting
  load/save helpers.
- `src/code_review/cli.py` — update the import of `record_learned_practices` /
  `merge_learned_extensions` to come from `review_planner.learning` instead of
  `review_planner.init`.

**Interfaces:** No public function signatures change — this is a pure relocation, not a
rewrite. `render_tool_setup_results(results: list[dict]) -> str` and
`record_learned_practices(...)` / `merge_learned_extensions(...)` keep identical
signatures and behavior.

**Dependencies:** `learning.py` depends on `io_utils.py` (or equivalent) plus stdlib
`json`/`pathlib`, matching what `init.py` currently uses for these functions. No new
external dependencies.

**Constraints:**

- No behavior change — structural refactor only, verified by the existing test suite
  passing unchanged (aside from import-path updates).
- `init.py` must not import from `render.py` after the change — no new coupling
  introduced in that direction.
- `init.py`'s existing `promote_accepted_feedback_to_learnings` (init.py:697) calls
  `record_learned_practices` directly (init.py:739) — this is a legitimate, pre-existing
  one-directional dependency (Init's feedback-promotion flow consuming Learning's public
  API), not a reversal of the coupling this spec fixes. `init.py` MAY import
  `record_learned_practices` from `review_planner.learning` for this one call site. No
  other new imports from `learning.py` into `init.py` are permitted, and `learning.py`
  must not import from `init.py`.
- Public import paths used by `tests/test_init.py` and `tests/test_plugins_and_migration.py`
  must be updated to the new module locations.

## Acceptance Criteria

1. `review_planner/init.py` no longer defines `render_tool_setup_results`,
   `record_learned_practices`, `merge_learned_extensions`, or their supporting
   load/save helpers.
2. `review_planner/render.py` defines `render_tool_setup_results` locally, importing
   only the renamed public `platform_label()` from `init.py` (no import of
   `render_tool_setup_results` itself from `init.py`).
3. A new `review_planner/learning.py` module defines `record_learned_practices` and
   `merge_learned_extensions` (plus supporting helpers), with behavior unchanged.
4. `cli.py` imports `record_learned_practices` / `merge_learned_extensions` from
   `review_planner.learning`.
5. Full test suite (`uv run pytest -q tests`) passes with zero behavior changes —
   existing tests may need import-path updates only, not logic changes.
6. `uvx ruff check` and `uv run pyrefly check` remain clean (no new lint/type errors).
7. No circular imports introduced: `learning.py` must not import from `init.py` or
   `render.py`. The only permitted cross-module imports are (a) `init.py → learning.py`
   for `record_learned_practices`, used solely by `promote_accepted_feedback_to_learnings`,
   and (b) `render.py → init.py` for the renamed public `platform_label()`. No other new
   import directions are introduced.

## Consistency Gate

- [x] Intent is unambiguous
- [x] Every behavior/goal maps to an acceptance criterion
- [x] Architecture constrains without over-engineering
- [x] Terminology consistent across artifacts
- [x] No contradictions between artifacts

**Verdict: PASS**
