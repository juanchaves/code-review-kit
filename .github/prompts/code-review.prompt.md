---
agent: code-review
---

Start immediately: run the code-review workflow on the current repository.

Workflow contract:
1. Run selected deterministic tools first and report exact execution status.
2. Execute both axes: standards and spec/requirements.
3. Return only actionable, line-anchored findings with severity + confidence.
4. For Azure DevOps tasks, prefer the `azure-devops-cli` skill/agent path.
5. End with two options: post PR comments (if PR exists) or generate implementation plan.

If repository context is unavailable, ask one concise question for the target path/branch/diff and continue.
