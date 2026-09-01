from __future__ import annotations

import html
import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .plugins.providers import ProviderRegistry, build_default_provider_registry


@dataclass(frozen=True)
class RequirementCandidate:
    text: str
    source: str
    confidence: str


def _normalize(text: str) -> str:
    return " ".join(text.strip().split())


def _dedupe(candidates: list[RequirementCandidate]) -> list[RequirementCandidate]:
    seen: set[str] = set()
    output: list[RequirementCandidate] = []
    for candidate in candidates:
        key = _normalize(candidate.text).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(candidate)
    return output


def _extract_markdown_bullets(text: str) -> list[str]:
    lines = text.splitlines()
    bullets: list[str] = []
    fallback_bullets: list[str] = []
    in_requirements_region = False
    for line in lines:
        lowered = line.lower().strip()
        if lowered.startswith("#"):
            in_requirements_region = any(
                token in lowered for token in ("requirement", "acceptance", "behavior", "scope")
            )
            continue
        candidate = ""
        if lowered.startswith(("- ", "* ", "+ ")):
            candidate = line[2:].strip()
        elif re.match(r"^\d+\.\s+", lowered):
            candidate = re.sub(r"^\d+\.\s+", "", line).strip()
        if candidate:
            fallback_bullets.append(candidate)
            if in_requirements_region:
                bullets.append(candidate)
    return bullets or fallback_bullets


def _strip_html(raw: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", raw)
    return html.unescape(_normalize(without_tags))


def detect_issue_provider(*, issue_ref: str, issue_provider: str, target: Path) -> str:
    registry = build_default_provider_registry()
    provider = registry.resolve_issue_provider(issue_ref=issue_ref, issue_provider=issue_provider, target=target)
    return provider.id


def _provider_from_git_remote(target: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(target), "config", "--get", "remote.origin.url"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    if completed.returncode != 0:
        return None
    remote = completed.stdout.strip().lower()
    if "dev.azure.com" in remote or "visualstudio.com" in remote:
        return "ado"
    if "github.com" in remote:
        return "github"
    return None


def _run_json_command(
    command: list[str], *, missing_tool_note: str, failed_note: str
) -> tuple[dict | None, str | None]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None, missing_tool_note
    if completed.returncode != 0:
        return None, failed_note
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None, failed_note
    if not isinstance(payload, dict):
        return None, failed_note
    return payload, None


def _read_github_issue_requirements(issue_ref: str) -> tuple[list[str], str | None]:
    payload, note = _run_json_command(
        ["gh", "issue", "view", issue_ref, "--json", "title,body"],
        missing_tool_note="GitHub CLI not available; issue-derived requirements were skipped.",
        failed_note="GitHub issue lookup failed; issue-derived requirements were skipped.",
    )
    if note or payload is None:
        return [], note
    body = payload.get("body", "")
    title = payload.get("title", "")
    if not isinstance(body, str):
        return [], "GitHub issue body format was invalid; issue-derived requirements were skipped."
    items = _extract_markdown_bullets(body)
    if not items and isinstance(title, str) and _normalize(title):
        items = [title]
    return items, None


def _extract_ado_work_item_id(issue_ref: str) -> str | None:
    candidate = issue_ref.strip().lstrip("#")
    if candidate.isdigit():
        return candidate
    matched = re.search(r"workitems/edit/(\d+)", issue_ref, flags=re.IGNORECASE)
    if matched:
        return matched.group(1)
    return None


def _read_ado_issue_requirements(issue_ref: str) -> tuple[list[str], str | None]:
    work_item_id = _extract_ado_work_item_id(issue_ref)
    if not work_item_id:
        return [], "ADO issue reference must be a numeric work item id or work item URL."

    payload, note = _run_json_command(
        ["az", "boards", "work-item", "show", "--id", work_item_id, "--output", "json"],
        missing_tool_note="Azure CLI not available; ADO issue-derived requirements were skipped.",
        failed_note="ADO issue lookup failed; issue-derived requirements were skipped.",
    )
    if note or payload is None:
        return [], note

    fields = payload.get("fields", {})
    if not isinstance(fields, dict):
        return [], "ADO issue payload was invalid; issue-derived requirements were skipped."
    description = fields.get("System.Description", "")
    title = fields.get("System.Title", "")
    text = _strip_html(description) if isinstance(description, str) else ""
    items = _extract_markdown_bullets(text)
    if not items and isinstance(title, str) and _normalize(title):
        items = [title]
    return items, None


def _jira_doc_to_text(node: object) -> str:
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        pieces: list[str] = []
        text = node.get("text")
        if isinstance(text, str):
            pieces.append(text)
        content = node.get("content")
        if isinstance(content, list):
            for item in content:
                nested = _jira_doc_to_text(item)
                if nested:
                    pieces.append(nested)
        return "\n".join(pieces)
    if isinstance(node, list):
        return "\n".join(filter(None, (_jira_doc_to_text(item) for item in node)))
    return ""


def _extract_jira_issue_key(issue_ref: str) -> str:
    if re.match(r"^[A-Z][A-Z0-9]+-\d+$", issue_ref):
        return issue_ref
    matched = re.search(r"/browse/([A-Z][A-Z0-9]+-\d+)", issue_ref)
    if matched:
        return matched.group(1)
    return issue_ref


def _read_jira_issue_requirements(issue_ref: str) -> tuple[list[str], str | None]:
    issue_key = _extract_jira_issue_key(issue_ref)
    jira_cli = (os.environ.get("JIRA_CLI") or "jira").strip()
    command_variants = [
        [jira_cli, "issue", "view", issue_key, "--plain", "--format", "json"],
        [jira_cli, "view", issue_key, "--plain", "--format", "json"],
    ]
    payload = None
    note = None
    for command in command_variants:
        payload, note = _run_json_command(
            command,
            missing_tool_note="Jira CLI not available; Jira issue-derived requirements were skipped.",
            failed_note="Jira issue lookup failed; issue-derived requirements were skipped.",
        )
        if payload is not None:
            break
    if payload is None:
        return [], note

    summary = payload.get("summary")
    description = payload.get("description")
    if summary is None and isinstance(payload.get("fields"), dict):
        fields = payload["fields"]
        summary = fields.get("summary")
        description = fields.get("description")

    text = _jira_doc_to_text(description)
    items = _extract_markdown_bullets(text)
    if not items and isinstance(summary, str) and _normalize(summary):
        items = [summary]
    return items, None


def _read_issue_requirements(
    *,
    issue_ref: str,
    issue_provider: str,
    target: Path,
    provider_registry: ProviderRegistry | None = None,
) -> tuple[list[str], list[str], str]:
    registry = provider_registry or build_default_provider_registry()
    plugin = registry.resolve_issue_provider(issue_ref=issue_ref, issue_provider=issue_provider, target=target)
    result = plugin.read_requirements(issue_ref, target=target)
    notes = [result.note] if result.note else []
    notes.append(f"Issue provider resolved to `{result.provider_id}`.")
    return result.requirements, notes, result.provider_id


def _read_doc_requirements(target: Path) -> list[str]:
    candidates: list[str] = []
    doc_paths: list[Path] = []
    readme = target / "README.md"
    if readme.exists():
        doc_paths.append(readme)
    docs_dir = target / "docs"
    if docs_dir.exists():
        doc_paths.extend(path for path in docs_dir.rglob("*.md") if path.is_file())
    for path in doc_paths[:12]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        candidates.extend(_extract_markdown_bullets(text))
    return candidates


def _infer_from_tests(target: Path) -> list[str]:
    inferred: list[str] = []
    patterns = ("test_*.py", "*_test.py", "*.spec.ts", "*.test.ts", "*.spec.js", "*.test.js")
    for pattern in patterns:
        for path in target.rglob(pattern):
            if not path.is_file():
                continue
            stem = path.stem.replace("test_", "").replace("_test", "")
            normalized = _normalize(stem.replace("_", " ").replace(".", " "))
            if normalized:
                inferred.append(f"Behavior covered by tests should remain stable for: {normalized}.")
    return inferred[:20]


def derive_requirements(
    *,
    target: Path,
    issue_ref: str | None = None,
    issue_provider: str = "auto",
    user_requirements: list[str] | None = None,
) -> dict:
    candidates: list[RequirementCandidate] = []
    notes: list[str] = []

    for requirement in user_requirements or []:
        if _normalize(requirement):
            candidates.append(RequirementCandidate(text=requirement, source="user", confidence="high"))

    resolved_issue_provider = None
    if issue_ref:
        issue_items, issue_notes, resolved_issue_provider = _read_issue_requirements(
            issue_ref=issue_ref,
            issue_provider=issue_provider,
            target=target,
            provider_registry=build_default_provider_registry(),
        )
        notes.extend(issue_notes)
        for item in issue_items:
            candidates.append(RequirementCandidate(text=item, source="issue", confidence="high"))

    for item in _read_doc_requirements(target):
        candidates.append(RequirementCandidate(text=item, source="docs", confidence="medium"))

    for item in _infer_from_tests(target):
        candidates.append(RequirementCandidate(text=item, source="tests", confidence="low"))

    unique = _dedupe(candidates)
    return {
        "source_order": ["issue", "docs", "tests", "user"],
        "issue_ref": issue_ref,
        "issue_provider": resolved_issue_provider or (issue_provider if issue_provider != "auto" else None),
        "enabled": True,
        "notes": notes,
        "requirements": [
            {"id": index + 1, "text": candidate.text, "source": candidate.source, "confidence": candidate.confidence}
            for index, candidate in enumerate(unique)
        ],
    }


def apply_walkthrough_overrides(
    *,
    requirements: dict,
    removed_ids: set[int],
    added_items: list[str],
) -> dict:
    current = requirements.get("requirements", [])
    kept = [item for item in current if isinstance(item, dict) and item.get("id") not in removed_ids]
    new_items = [
        {"id": 0, "text": text, "source": "user", "confidence": "high"}
        for text in (_normalize(item) for item in added_items)
        if text
    ]
    merged = kept + new_items
    renumbered = [
        {"id": index + 1, "text": item["text"], "source": item["source"], "confidence": item["confidence"]}
        for index, item in enumerate(merged)
    ]
    return {**requirements, "requirements": renumbered, "walkthrough_confirmed": True}


def apply_grilling_refinement(
    *,
    requirements: dict,
    ask: Callable[[str], str] = input,
) -> dict:
    added_items: list[str] = []
    prompts = [
        "What outcome must be true for this change to be successful?",
        "What failure mode would be unacceptable in production?",
        "What constraint (security, compliance, performance, UX) must not be violated?",
        "What verification signal proves this requirement is satisfied?",
    ]
    for prompt in prompts:
        answer = _normalize(ask(f"{prompt}\n> "))
        if answer:
            added_items.append(answer)

    while True:
        extra = _normalize(ask("Add another requirement (blank to finish): "))
        if not extra:
            break
        added_items.append(extra)

    updated = apply_walkthrough_overrides(requirements=requirements, removed_ids=set(), added_items=added_items)
    notes = list(updated.get("notes", [])) if isinstance(updated.get("notes", []), list) else []
    notes.append("Requirements refined with grilling mode.")
    updated["notes"] = notes
    updated["requirements_refiner"] = "grilling"
    return updated
