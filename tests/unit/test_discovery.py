"""Unit tests for the migration discovery module."""

import subprocess
from pathlib import Path

import pytest

from aperture.config import Config
from aperture.discovery import (
    DiscoveryError,
    discover_files,
    discover_from_git_diff,
    discover_from_globs,
    discover_from_paths,
)


def test_discover_from_explicit_paths(tmp_path: Path) -> None:
    """Verify discovery from explicit file and directory paths."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()

    f1 = migrations_dir / "001.sql"
    f1.write_text("SELECT 1;", encoding="utf-8")

    f2 = migrations_dir / "002.sql"
    f2.write_text("SELECT 2;", encoding="utf-8")

    non_sql = migrations_dir / "notes.txt"
    non_sql.write_text("notes", encoding="utf-8")

    # Pass individual file
    results = discover_from_paths([f1])
    assert results == [f1.resolve()]

    # Pass directory containing sql and non-sql
    results = discover_from_paths([migrations_dir])
    assert results == sorted([f1.resolve(), f2.resolve()])


def test_discover_from_paths_not_found(tmp_path: Path) -> None:
    """Verify DiscoveryError is raised when path does not exist."""
    missing = tmp_path / "missing"
    with pytest.raises(DiscoveryError, match="Migration path does not exist"):
        discover_from_paths([missing])


def test_discover_from_globs(tmp_path: Path) -> None:
    """Verify discovery using glob patterns."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()

    m1 = db_dir / "001_init.sql"
    m1.write_text("SELECT 1;", encoding="utf-8")

    m2 = db_dir / "002_add.sql"
    m2.write_text("SELECT 2;", encoding="utf-8")

    ignore = db_dir / "readme.md"
    ignore.write_text("# Readme", encoding="utf-8")

    results = discover_from_globs(["db/*.sql"], root_dir=tmp_path)
    assert results == sorted([m1.resolve(), m2.resolve()])


def test_discover_from_git_diff_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify discover_from_git_diff parses git output and filters to existing sql files."""
    f1 = tmp_path / "migrations" / "001_new.sql"
    f1.parent.mkdir()
    f1.write_text("SELECT 1;", encoding="utf-8")

    # Mock subprocess.run
    fake_stdout = "migrations/001_new.sql\nsrc/app.py\nmigrations/deleted.sql\n"

    def mock_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["git", "diff"],
            returncode=0,
            stdout=fake_stdout,
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", mock_run)

    results = discover_from_git_diff("origin/main", root_dir=tmp_path)
    # migrations/deleted.sql doesn't exist on disk, so it should be excluded
    # src/app.py is not .sql, so excluded
    assert results == [f1.resolve()]


def test_discover_from_git_diff_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify git diff failure raises clear DiscoveryError."""

    def mock_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["git", "diff"],
            returncode=128,
            stdout="",
            stderr="fatal: ambiguous argument 'invalid-ref': unknown revision\n",
        )

    monkeypatch.setattr(subprocess, "run", mock_run)

    with pytest.raises(DiscoveryError, match="Git diff failed for target"):
        discover_from_git_diff("invalid-ref")


def test_discover_from_git_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify missing git binary raises clear DiscoveryError."""

    def mock_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(subprocess, "run", mock_run)

    with pytest.raises(DiscoveryError, match="Git executable 'git' not found"):
        discover_from_git_diff("main")


def test_discover_files_priority(tmp_path: Path) -> None:
    """Verify discover_files respects priority order (paths > base_ref > config globs)."""
    f = tmp_path / "explicit.sql"
    f.write_text("SELECT 1;", encoding="utf-8")

    # Explicit path takes top priority
    res = discover_files(paths=[f])
    assert res == [f.resolve()]

    # Config globs fallback when no paths or base_ref
    cfg = Config(migration_paths=["*.sql"])
    res = discover_files(config=cfg, root_dir=tmp_path)
    assert res == [f.resolve()]
