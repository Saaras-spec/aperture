"""Unit tests for GitHub Action runner."""

from pathlib import Path

import pytest

from aperture.action_runner import run_action


def test_action_runner_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify action_runner runs cleanly and outputs findings-count=0 and passed=true."""
    migration_file = tmp_path / "001_safe.sql"
    migration_file.write_text("CREATE TABLE users (id int);", encoding="utf-8")

    cfg_file = tmp_path / "aperture.yml"
    cfg_file.write_text("fail_on: ERROR\n", encoding="utf-8")

    output_file = tmp_path / "github_output.txt"
    output_file.touch()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INPUT_CONFIG", str(cfg_file))
    monkeypatch.setenv("INPUT_COMMENT_ON_PR", "false")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

    # Mock discover_files to return our safe migration
    monkeypatch.setattr(
        "aperture.action_runner.discover_files",
        lambda **_kwargs: [migration_file],
    )

    exit_code = run_action()
    assert exit_code == 0

    outputs = output_file.read_text(encoding="utf-8")
    assert "findings-count=0" in outputs
    assert "passed=true" in outputs


def test_action_runner_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify action_runner exits 1 and outputs passed=false when findings exist."""
    migration_file = tmp_path / "002_risky.sql"
    migration_file.write_text("DROP TABLE users;", encoding="utf-8")

    output_file = tmp_path / "github_output.txt"
    output_file.touch()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INPUT_COMMENT_ON_PR", "false")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

    monkeypatch.setattr(
        "aperture.action_runner.discover_files",
        lambda **_kwargs: [migration_file],
    )

    exit_code = run_action()
    assert exit_code == 1

    outputs = output_file.read_text(encoding="utf-8")
    assert "findings-count=1" in outputs
    assert "passed=false" in outputs


def test_action_runner_no_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify action_runner exits 0 when no migration files are discovered."""
    output_file = tmp_path / "github_output.txt"
    output_file.touch()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INPUT_COMMENT_ON_PR", "false")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

    monkeypatch.setattr(
        "aperture.action_runner.discover_files",
        lambda **_kwargs: [],
    )

    exit_code = run_action()
    assert exit_code == 0
    outputs = output_file.read_text(encoding="utf-8")
    assert "findings-count=0" in outputs
    assert "passed=true" in outputs


def test_action_runner_post_comment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify action_runner invokes post_or_update_comment when in PR context."""
    migration_file = tmp_path / "001_safe.sql"
    migration_file.write_text("CREATE TABLE t (id int);", encoding="utf-8")

    output_file = tmp_path / "github_output.txt"
    output_file.touch()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INPUT_COMMENT_ON_PR", "true")
    monkeypatch.setenv("INPUT_GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

    monkeypatch.setattr(
        "aperture.action_runner.discover_files",
        lambda **_kwargs: [migration_file],
    )
    monkeypatch.setattr(
        "aperture.action_runner.get_pr_context_from_env",
        lambda: ("owner/repo", 42),
    )

    posted: list[tuple[str, str, int, str]] = []

    def mock_post(token: str, repo: str, pr_num: int, body: str) -> int:
        posted.append((token, repo, pr_num, body))
        return 777

    monkeypatch.setattr("aperture.action_runner.post_or_update_comment", mock_post)

    exit_code = run_action()
    assert exit_code == 0
    assert len(posted) == 1
    assert posted[0][0] == "fake-token"
    assert posted[0][1] == "owner/repo"
    assert posted[0][2] == 42
