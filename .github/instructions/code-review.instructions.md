---
applyTo: "**/*"
---

# code-review review workflow

- Keep personas, baseline packs, tool packs, language packs, specialty packs, and strategies selectable.
- Keep prompt context compact: changed-files-first scope, selected checks, and tool evidence only.
- Prefer deterministic gates before parallel subagent review.
- Use two review axes: standards compliance and spec/requirements compliance.
- Favor evidence-based findings with file+line references and concrete remediation.
- For harness runs started via `/agent code-review` (or equivalent), auto-start review on the current repository.
- Treat selected-tool execution as mandatory: if required tools were skipped, surface that as an explicit failure.
- After review, provide exactly two completion choices: post actionable PR comments (when PR exists) or generate an implementation plan.
- Keep startup and post-review behavior consistent across Copilot, Claude Code, and OpenCode.
- Execute approved plans end-to-end: complete all dependent phases and steps before stopping unless requirements are ambiguous.
