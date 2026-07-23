from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GovernanceDecision:
    approved: bool
    reason: str
    evidence: dict


class GovernancePlugin(Protocol):
    id: str

    def decide_pr_publish(self, *, requested_action: str, active_pr: dict | None, interactive: bool) -> GovernanceDecision: ...


class StrictHumanApprovalGovernance:
    id = "strict-human-approval"

    def decide_pr_publish(self, *, requested_action: str, active_pr: dict | None, interactive: bool) -> GovernanceDecision:
        if requested_action != "comment":
            return GovernanceDecision(
                approved=True,
                reason="No publish side effect requested.",
                evidence={"requested_action": requested_action, "side_effect": False},
            )
        if active_pr is None:
            return GovernanceDecision(
                approved=False,
                reason="Publish blocked: no active pull request detected.",
                evidence={"requested_action": requested_action, "side_effect": True, "active_pr": False},
            )
        if not interactive:
            return GovernanceDecision(
                approved=False,
                reason="Publish blocked: explicit human approval required in interactive mode.",
                evidence={"requested_action": requested_action, "side_effect": True, "interactive": False},
            )
        answer = input("Approve publishing PR comments now? Type 'approve' to continue: ").strip().lower()
        if answer != "approve":
            return GovernanceDecision(
                approved=False,
                reason="Publish blocked: approval token not provided.",
                evidence={"requested_action": requested_action, "side_effect": True, "approval_token": "missing"},
            )
        return GovernanceDecision(
            approved=True,
            reason="Publish approved by human.",
            evidence={"requested_action": requested_action, "side_effect": True, "approval_token": "approve"},
        )


class LenientGovernance:
    id = "lenient"

    def decide_pr_publish(self, *, requested_action: str, active_pr: dict | None, interactive: bool) -> GovernanceDecision:
        if requested_action == "comment" and active_pr is None:
            return GovernanceDecision(
                approved=False,
                reason="Publish blocked: no active PR detected.",
                evidence={"requested_action": requested_action, "active_pr": False},
            )
        return GovernanceDecision(
            approved=True,
            reason="Action allowed by lenient governance policy.",
            evidence={"requested_action": requested_action, "interactive": interactive},
        )


@dataclass
class GovernanceRegistry:
    plugins: dict[str, GovernancePlugin]

    def resolve(self, plugin_id: str | None) -> GovernancePlugin:
        selected = (plugin_id or "").strip()
        if selected:
            plugin = self.plugins.get(selected)
            if plugin is None:
                available = ", ".join(sorted(self.plugins))
                raise ValueError(f"Unknown governance plugin '{selected}'. Available: {available}")
            return plugin
        return self.plugins["strict-human-approval"]


def build_default_governance_registry() -> GovernanceRegistry:
    return GovernanceRegistry(
        plugins={
            "strict-human-approval": StrictHumanApprovalGovernance(),
            "lenient": LenientGovernance(),
        }
    )
