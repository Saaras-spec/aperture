"""GitHub PR comment integration for MigrationGuard.

Posts or updates a single PR comment using the GitHub REST API.
Comments are identified by the hidden marker <!-- migrationguard -->.
Token security: tokens are never printed, logged, or exposed in error messages.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

_API_URL = "https://api.github.com"
_MARKER = "<!-- migrationguard -->"


class GitHubCommentError(Exception):
    """Raised when GitHub API operations fail."""


def find_migrationguard_comment(
    token: str,
    repo: str,
    pr_number: int,
    api_url: str = _API_URL,
) -> int | None:
    """Find existing MigrationGuard comment ID on a pull request.

    Args:
        token: GitHub API token.
        repo: Repository in 'owner/repo' format.
        pr_number: Pull request issue number.
        api_url: Base GitHub API URL.

    Returns:
        Comment ID if found, None otherwise.
    """
    url = f"{api_url}/repos/{repo}/issues/{pr_number}/comments?per_page=100"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "MigrationGuard-Action",
    }

    req = urllib.request.Request(url, headers=headers, method="GET")

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        raise GitHubCommentError(
            f"Failed to list PR comments (HTTP {err.code}): {err.reason}"
        ) from None
    except Exception as err:
        raise GitHubCommentError(f"Network error while listing PR comments: {err}") from None

    if isinstance(data, list):
        for comment in data:
            body = comment.get("body", "")
            if _MARKER in body:
                comment_id = comment.get("id")
                if isinstance(comment_id, int):
                    return comment_id

    return None


def post_or_update_comment(
    token: str,
    repo: str,
    pr_number: int,
    body: str,
    api_url: str = _API_URL,
) -> int:
    """Post a new comment or update existing MigrationGuard comment on a PR.

    Args:
        token: GitHub API token.
        repo: Repository in 'owner/repo' format.
        pr_number: Pull request issue number.
        body: Markdown content of the comment.
        api_url: Base GitHub API URL.

    Returns:
        Comment ID of the created or updated comment.

    Raises:
        GitHubCommentError: If API call fails.
    """
    if not token or not token.strip():
        raise GitHubCommentError("GitHub token must not be empty.")

    if not body.strip():
        raise GitHubCommentError("Comment body must not be empty.")

    existing_id = find_migrationguard_comment(token, repo, pr_number, api_url=api_url)

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "MigrationGuard-Action",
    }
    payload = json.dumps({"body": body}).encode("utf-8")

    if existing_id is not None:
        # Update existing comment
        url = f"{api_url}/repos/{repo}/issues/comments/{existing_id}"
        req = urllib.request.Request(url, data=payload, headers=headers, method="PATCH")
        action = "update"
    else:
        # Create new comment
        url = f"{api_url}/repos/{repo}/issues/{pr_number}/comments"
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        action = "create"

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            cid = data.get("id")
            if isinstance(cid, int):
                return cid
            return existing_id or 0
    except urllib.error.HTTPError as err:
        raise GitHubCommentError(
            f"Failed to {action} PR comment (HTTP {err.code}): {err.reason}"
        ) from None
    except Exception as err:
        raise GitHubCommentError(
            f"Network error while trying to {action} PR comment: {err}"
        ) from None


def get_pr_context_from_env(event_path: str | Path | None = None) -> tuple[str, int] | None:
    """Extract repo and PR number from GitHub Actions environment.

    Args:
        event_path: Optional path to GitHub event JSON file.

    Returns:
        Tuple of (repo, pr_number) or None if not running in a pull_request event.
    """
    import os

    repo = os.getenv("GITHUB_REPOSITORY")
    if not repo:
        return None

    path = Path(event_path) if event_path else Path(os.getenv("GITHUB_EVENT_PATH", ""))
    if not path.is_file():
        return None

    try:
        event_data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    # Check pull_request payload
    pr_data = event_data.get("pull_request")
    if isinstance(pr_data, dict) and "number" in pr_data:
        pr_number = pr_data["number"]
        if isinstance(pr_number, int):
            return repo, pr_number

    # Check issue payload (for issue_comment events)
    issue_data = event_data.get("issue")
    if isinstance(issue_data, dict) and "number" in issue_data:
        issue_number = issue_data["number"]
        if isinstance(issue_number, int):
            return repo, issue_number

    return None
