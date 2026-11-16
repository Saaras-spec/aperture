"""Unit tests for GitHub PR comment posting and updating."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from migrationguard.github_comment import (
    GitHubCommentError,
    find_migrationguard_comment,
    get_pr_context_from_env,
    post_or_update_comment,
)


class FakeHTTPResponse:
    """Mock urllib response object."""

    def __init__(self, data: object, status: int = 200) -> None:
        self.data = json.dumps(data).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self.data

    def __enter__(self) -> FakeHTTPResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        pass


def test_find_comment_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify finding existing comment containing the migrationguard marker."""
    comments = [
        {"id": 101, "body": "Regular review comment"},
        {"id": 202, "body": "<!-- migrationguard -->\n## 🛡️ MigrationGuard Report\n..."},
    ]

    def mock_urlopen(req: urllib.request.Request) -> FakeHTTPResponse:
        assert "Authorization" in req.headers
        assert "Bearer secret-token" in req.headers["Authorization"]
        return FakeHTTPResponse(comments)

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    cid = find_migrationguard_comment("secret-token", "owner/repo", 42)
    assert cid == 202


def test_find_comment_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify returns None when no comment matches the marker."""
    comments = [
        {"id": 101, "body": "Just a normal comment"},
    ]

    def mock_urlopen(_req: urllib.request.Request) -> FakeHTTPResponse:
        return FakeHTTPResponse(comments)

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    cid = find_migrationguard_comment("secret-token", "owner/repo", 42)
    assert cid is None


def test_find_comment_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify HTTPError is wrapped in GitHubCommentError without leaking token."""

    def mock_urlopen(_req: urllib.request.Request) -> FakeHTTPResponse:
        raise urllib.error.HTTPError(
            url="https://api.github.com/...",
            code=403,
            msg="Forbidden",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b"Forbidden error"),
        )

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    secret = "my-super-secret-token-12345"
    with pytest.raises(GitHubCommentError) as exc_info:
        find_migrationguard_comment(secret, "owner/repo", 42)

    assert "HTTP 403" in str(exc_info.value)
    # Ensure token is NEVER leaked in error message
    assert secret not in str(exc_info.value)


def test_post_comment_creates_new(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify posts new comment when no existing comment is found."""
    # 1. find_comment returns empty list -> None
    # 2. post creates comment with ID 303
    calls: list[str] = []

    def mock_urlopen(req: urllib.request.Request) -> FakeHTTPResponse:
        calls.append(req.get_method())
        if req.get_method() == "GET":
            return FakeHTTPResponse([])
        if req.get_method() == "POST":
            assert isinstance(req.data, bytes)
            payload = json.loads(req.data.decode("utf-8"))
            assert "<!-- migrationguard -->" in payload["body"]
            return FakeHTTPResponse({"id": 303, "body": payload["body"]})
        raise AssertionError(f"Unexpected method: {req.get_method()}")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    body = "<!-- migrationguard -->\nNew report"
    cid = post_or_update_comment("test-token", "owner/repo", 99, body)
    assert cid == 303
    assert calls == ["GET", "POST"]


def test_post_comment_updates_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify updates existing comment via PATCH when marker is found."""
    existing_comment = [{"id": 404, "body": "<!-- migrationguard --> old report"}]
    calls: list[str] = []

    def mock_urlopen(req: urllib.request.Request) -> FakeHTTPResponse:
        calls.append(req.get_method())
        if req.get_method() == "GET":
            return FakeHTTPResponse(existing_comment)
        if req.get_method() == "PATCH":
            assert "/comments/404" in req.full_url
            return FakeHTTPResponse({"id": 404, "body": "updated report"})
        raise AssertionError(f"Unexpected method: {req.get_method()}")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    body = "<!-- migrationguard -->\nUpdated report"
    cid = post_or_update_comment("test-token", "owner/repo", 99, body)
    assert cid == 404
    assert calls == ["GET", "PATCH"]


def test_post_comment_validation() -> None:
    """Verify validation on empty tokens or bodies."""
    with pytest.raises(GitHubCommentError, match="token must not be empty"):
        post_or_update_comment("", "owner/repo", 1, "body")

    with pytest.raises(GitHubCommentError, match="body must not be empty"):
        post_or_update_comment("token", "owner/repo", 1, "")


def test_get_pr_context_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify extracting PR context from GitHub event JSON file."""
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps({"pull_request": {"number": 123}}), encoding="utf-8")

    monkeypatch.setenv("GITHUB_REPOSITORY", "myorg/myrepo")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))

    res = get_pr_context_from_env()
    assert res == ("myorg/myrepo", 123)


def test_get_pr_context_not_in_pr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify returns None when not running in a pull request event."""
    event_file = tmp_path / "push_event.json"
    event_file.write_text(json.dumps({"ref": "refs/heads/main"}), encoding="utf-8")

    monkeypatch.setenv("GITHUB_REPOSITORY", "myorg/myrepo")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))

    res = get_pr_context_from_env()
    assert res is None


def test_get_pr_context_issue_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify extracting issue number if pull_request key is absent."""
    event_file = tmp_path / "issue_event.json"
    event_file.write_text(json.dumps({"issue": {"number": 789}}), encoding="utf-8")

    monkeypatch.setenv("GITHUB_REPOSITORY", "myorg/myrepo")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))

    res = get_pr_context_from_env()
    assert res == ("myorg/myrepo", 789)


def test_post_comment_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify HTTP error during comment post raises GitHubCommentError."""
    monkeypatch.setattr(
        "migrationguard.github_comment.find_migrationguard_comment",
        lambda *_args, **_kwargs: None,
    )

    def mock_urlopen(_req: urllib.request.Request) -> FakeHTTPResponse:
        raise urllib.error.HTTPError(
            url="https://api.github.com/...",
            code=401,
            msg="Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b"Unauthorized"),
        )

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    with pytest.raises(GitHubCommentError, match="HTTP 401"):
        post_or_update_comment("bad-token", "owner/repo", 1, "body")
