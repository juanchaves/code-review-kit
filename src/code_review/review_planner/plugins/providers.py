from __future__ import annotations

from dataclasses import dataclass
import html
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse


def normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


def extract_markdown_bullets(text: str) -> list[str]:
    lines = text.splitlines()
    bullets: list[str] = []
    fallback_bullets: list[str] = []
    in_requirements_region = False
    for line in lines:
        lowered = line.lower().strip()
        if lowered.startswith("#"):
            in_requirements_region = any(token in lowered for token in ("requirement", "acceptance", "behavior", "scope"))
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


def strip_html(raw: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", raw)
    return html.unescape(normalize_text(without_tags))


@dataclass(frozen=True)
class IssueProviderResult:
    provider_id: str
    requirements: list[str]
    note: str | None = None


class IssueProviderPlugin(Protocol):
    id: str

    def read_requirements(self, issue_ref: str, *, target: Path) -> IssueProviderResult: ...


class PrProviderPlugin(Protocol):
    id: str

    def detect_pull_request(self, *, review_target: Path, pr_ref: str | None = None) -> dict | None: ...

    def publish_comment(self, *, review_target: Path, pull_request: dict, body: str) -> str: ...


def provider_from_git_remote(target: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(target), "config", "--get", "remote.origin.url"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    remote = completed.stdout.strip().lower()
    if "dev.azure.com" in remote or "visualstudio.com" in remote:
        return "ado"
    if "github.com" in remote:
        return "github"
    return None


def _run_json_command(command: list[str], *, missing_tool_note: str, failed_note: str, cwd: Path | None = None) -> tuple[dict | list | None, str | None]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            cwd=cwd,
        )
    except FileNotFoundError:
        return None, missing_tool_note
    if completed.returncode != 0:
        return None, failed_note
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None, failed_note
    return payload, None


class GitHubIssueProvider:
    id = "github"

    def read_requirements(self, issue_ref: str, *, target: Path) -> IssueProviderResult:
        payload, note = _run_json_command(
            ["gh", "issue", "view", issue_ref, "--json", "title,body"],
            missing_tool_note="GitHub CLI not available; issue-derived requirements were skipped.",
            failed_note="GitHub issue lookup failed; issue-derived requirements were skipped.",
            cwd=target,
        )
        if note or not isinstance(payload, dict):
            return IssueProviderResult(provider_id=self.id, requirements=[], note=note)
        body = payload.get("body", "")
        title = payload.get("title", "")
        if not isinstance(body, str):
            return IssueProviderResult(provider_id=self.id, requirements=[], note="GitHub issue body format was invalid; issue-derived requirements were skipped.")
        items = extract_markdown_bullets(body)
        if not items and isinstance(title, str) and normalize_text(title):
            items = [title]
        return IssueProviderResult(provider_id=self.id, requirements=items)


def extract_ado_work_item_id(issue_ref: str) -> str | None:
    candidate = issue_ref.strip().lstrip("#")
    if candidate.isdigit():
        return candidate
    matched = re.search(r"workitems/edit/(\d+)", issue_ref, flags=re.IGNORECASE)
    if matched:
        return matched.group(1)
    return None


class AdoIssueProvider:
    id = "ado"

    def read_requirements(self, issue_ref: str, *, target: Path) -> IssueProviderResult:
        work_item_id = extract_ado_work_item_id(issue_ref)
        if not work_item_id:
            return IssueProviderResult(provider_id=self.id, requirements=[], note="ADO issue reference must be a numeric work item id or work item URL.")
        payload, note = _run_json_command(
            ["az", "boards", "work-item", "show", "--id", work_item_id, "--output", "json"],
            missing_tool_note="Azure CLI not available; ADO issue-derived requirements were skipped.",
            failed_note="ADO issue lookup failed; issue-derived requirements were skipped.",
            cwd=target,
        )
        if note or not isinstance(payload, dict):
            return IssueProviderResult(provider_id=self.id, requirements=[], note=note)
        fields = payload.get("fields", {})
        if not isinstance(fields, dict):
            return IssueProviderResult(provider_id=self.id, requirements=[], note="ADO issue payload was invalid; issue-derived requirements were skipped.")
        description = fields.get("System.Description", "")
        title = fields.get("System.Title", "")
        text = strip_html(description) if isinstance(description, str) else ""
        items = extract_markdown_bullets(text)
        if not items and isinstance(title, str) and normalize_text(title):
            items = [title]
        return IssueProviderResult(provider_id=self.id, requirements=items)


def jira_doc_to_text(node: object) -> str:
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
                nested = jira_doc_to_text(item)
                if nested:
                    pieces.append(nested)
        return "\n".join(pieces)
    if isinstance(node, list):
        return "\n".join(filter(None, (jira_doc_to_text(item) for item in node)))
    return ""


def extract_jira_issue_key(issue_ref: str) -> str:
    if re.match(r"^[A-Z][A-Z0-9]+-\d+$", issue_ref):
        return issue_ref
    matched = re.search(r"/browse/([A-Z][A-Z0-9]+-\d+)", issue_ref)
    if matched:
        return matched.group(1)
    return issue_ref


class JiraIssueProvider:
    id = "jira"

    def read_requirements(self, issue_ref: str, *, target: Path) -> IssueProviderResult:
        issue_key = extract_jira_issue_key(issue_ref)
        command_variants = [
            ["jira", "issue", "view", issue_key, "--plain", "--format", "json"],
            ["jira", "view", issue_key, "--plain", "--format", "json"],
        ]
        payload = None
        note = None
        for command in command_variants:
            payload, note = _run_json_command(
                command,
                missing_tool_note="Jira CLI not available; Jira issue-derived requirements were skipped.",
                failed_note="Jira issue lookup failed; issue-derived requirements were skipped.",
                cwd=target,
            )
            if payload is not None:
                break
        if not isinstance(payload, dict):
            return IssueProviderResult(provider_id=self.id, requirements=[], note=note)
        summary = payload.get("summary")
        description = payload.get("description")
        if summary is None and isinstance(payload.get("fields"), dict):
            fields = payload["fields"]
            summary = fields.get("summary")
            description = fields.get("description")
        text = jira_doc_to_text(description)
        items = extract_markdown_bullets(text)
        if not items and isinstance(summary, str) and normalize_text(summary):
            items = [summary]
        return IssueProviderResult(provider_id=self.id, requirements=items)


def parse_ado_pr_reference(pr_ref: str | None) -> dict:
    parsed_ref = {"id": None, "organization": None}
    if not pr_ref:
        return parsed_ref
    candidate = pr_ref.strip()
    if not candidate:
        return parsed_ref
    if candidate.isdigit():
        parsed_ref["id"] = candidate
        return parsed_ref
    matched = re.search(r"/pullrequest/(\d+)", candidate, flags=re.IGNORECASE)
    if matched:
        parsed_ref["id"] = matched.group(1)
    matched_query = re.search(r"[?&]pullrequestid=(\d+)", candidate, flags=re.IGNORECASE)
    if matched_query and not parsed_ref["id"]:
        parsed_ref["id"] = matched_query.group(1)
    if candidate.startswith(("http://", "https://")):
        parsed_url = urlparse(candidate)
        host = parsed_url.netloc.lower()
        if "dev.azure.com" in host:
            segments = [segment for segment in parsed_url.path.split("/") if segment]
            if segments:
                parsed_ref["organization"] = f"https://dev.azure.com/{segments[0]}"
        elif host.endswith(".visualstudio.com"):
            parsed_ref["organization"] = f"https://{parsed_url.netloc}"
    return parsed_ref


def current_branch(review_target: Path) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(review_target), "rev-parse", "--abbrev-ref", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    branch = completed.stdout.strip()
    return branch or None


class GitHubPrProvider:
    id = "github"

    def detect_pull_request(self, *, review_target: Path, pr_ref: str | None = None) -> dict | None:
        command = ["gh", "pr", "view"]
        if pr_ref:
            command.append(pr_ref)
        command.extend(["--json", "number,title,url,headRefName,baseRefName"])
        payload, note = _run_json_command(
            command,
            missing_tool_note="GitHub CLI not available.",
            failed_note="GitHub PR lookup failed.",
            cwd=review_target,
        )
        if note or not isinstance(payload, dict):
            return None
        if not isinstance(payload.get("number"), int):
            return None
        return {**payload, "provider": self.id}

    def publish_comment(self, *, review_target: Path, pull_request: dict, body: str) -> str:
        completed = subprocess.run(
            ["gh", "pr", "comment", str(pull_request["number"]), "--body", body],
            cwd=review_target,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
            raise ValueError(f"Failed to publish PR comment: {stderr}")
        return f"Posted actionable PR comment to #{pull_request['number']}."


class AdoPrProvider:
    id = "ado"

    def detect_pull_request(self, *, review_target: Path, pr_ref: str | None = None) -> dict | None:
        parsed_ref = parse_ado_pr_reference(pr_ref)
        organization = parsed_ref.get("organization")
        if parsed_ref.get("id"):
            command = ["az", "repos", "pr", "show", "--id", str(parsed_ref["id"]), "--output", "json"]
            if organization:
                command.extend(["--org", str(organization)])
        else:
            branch = current_branch(review_target)
            if not branch:
                return None
            command = [
                "az",
                "repos",
                "pr",
                "list",
                "--status",
                "active",
                "--source-branch",
                branch,
                "--top",
                "1",
                "--output",
                "json",
                "--detect",
                "true",
            ]
            if organization:
                command.extend(["--org", str(organization)])
        payload, note = _run_json_command(
            command,
            missing_tool_note="Azure CLI not available.",
            failed_note="ADO PR lookup failed.",
            cwd=review_target,
        )
        if note or payload is None:
            return None
        if isinstance(payload, list):
            if not payload:
                return None
            payload = payload[0]
        if not isinstance(payload, dict):
            return None
        pr_number_raw = payload.get("pullRequestId", payload.get("codeReviewId"))
        if not isinstance(pr_number_raw, int):
            return None
        repository = payload.get("repository", {})
        repo_id = repository.get("id") if isinstance(repository, dict) else None
        project = repository.get("project") if isinstance(repository, dict) else None
        project_name = project.get("name") if isinstance(project, dict) else None
        return {
            "number": pr_number_raw,
            "title": payload.get("title", ""),
            "url": payload.get("url", ""),
            "provider": self.id,
            "organization": organization,
            "project": project_name,
            "repository_id": repo_id,
        }

    def publish_comment(self, *, review_target: Path, pull_request: dict, body: str) -> str:
        project = pull_request.get("project")
        repository_id = pull_request.get("repository_id")
        if not isinstance(project, str) or not project.strip() or not isinstance(repository_id, str) or not repository_id.strip():
            raise ValueError("Unable to resolve ADO PR repository/project context for comment publication.")
        thread_payload = {
            "comments": [{"parentCommentId": 0, "content": body, "commentType": 1}],
            "status": "active",
        }
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as thread_handle:
            json.dump(thread_payload, thread_handle)
            thread_path = Path(thread_handle.name)
        command = [
            "az",
            "devops",
            "invoke",
            "--area",
            "git",
            "--resource",
            "pullRequestThreads",
            "--route-parameters",
            f"project={project}",
            f"repositoryId={repository_id}",
            f"pullRequestId={pull_request['number']}",
            "--http-method",
            "POST",
            "--api-version",
            "7.1",
            "--in-file",
            str(thread_path),
            "--output",
            "json",
        ]
        if isinstance(pull_request.get("organization"), str) and pull_request["organization"].strip():
            command.extend(["--org", pull_request["organization"]])
        try:
            completed = subprocess.run(
                command,
                cwd=review_target,
                check=False,
                capture_output=True,
                text=True,
            )
        finally:
            thread_path.unlink(missing_ok=True)
        if completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
            raise ValueError(f"Failed to publish PR comment: {stderr}")
        return f"Posted actionable PR comment to ADO PR #{pull_request['number']}."


@dataclass
class ProviderRegistry:
    issue_providers: dict[str, IssueProviderPlugin]
    pr_providers: dict[str, PrProviderPlugin]

    def resolve_issue_provider(self, *, issue_ref: str, issue_provider: str, target: Path) -> IssueProviderPlugin:
        if issue_provider in self.issue_providers:
            return self.issue_providers[issue_provider]
        lowered_ref = issue_ref.lower()
        if "dev.azure.com" in lowered_ref or "visualstudio.com" in lowered_ref:
            return self.issue_providers["ado"]
        if "atlassian.net" in lowered_ref or "/browse/" in lowered_ref or re.match(r"^[A-Z][A-Z0-9]+-\d+$", issue_ref):
            return self.issue_providers["jira"]
        if issue_ref.startswith("#") or issue_ref.isdigit():
            remote = provider_from_git_remote(target)
            if remote and remote in self.issue_providers:
                return self.issue_providers[remote]
        return self.issue_providers["github"]

    def resolve_pr_provider(self, *, review_target: Path, pr_ref: str | None = None) -> PrProviderPlugin:
        candidate = (pr_ref or "").strip().lower()
        if "dev.azure.com" in candidate or "visualstudio.com" in candidate:
            return self.pr_providers["ado"]
        if "github.com" in candidate:
            return self.pr_providers["github"]
        remote = provider_from_git_remote(review_target)
        if remote in self.pr_providers:
            return self.pr_providers[remote]
        return self.pr_providers["github"]


def build_default_provider_registry() -> ProviderRegistry:
    return ProviderRegistry(
        issue_providers={
            "github": GitHubIssueProvider(),
            "ado": AdoIssueProvider(),
            "jira": JiraIssueProvider(),
        },
        pr_providers={
            "github": GitHubPrProvider(),
            "ado": AdoPrProvider(),
        },
    )
