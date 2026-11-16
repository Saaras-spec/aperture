"""Migration file discovery via paths, globs, or git diff."""

from __future__ import annotations

import glob
import subprocess
from collections.abc import Sequence
from pathlib import Path

from aperture.config import Config


class DiscoveryError(Exception):
    """Raised when discovering migration files fails."""


def _is_sql_file(path: Path) -> bool:
    """Return True if path is a file with .sql extension."""
    return path.is_file() and path.suffix.lower() == ".sql"


def discover_from_paths(paths: Sequence[str | Path]) -> list[Path]:
    """Find migration files from explicit file or directory paths.

    Args:
        paths: List of file or directory paths.

    Returns:
        List of unique .sql file Paths.

    Raises:
        DiscoveryError: If a specified path does not exist.
    """
    discovered: set[Path] = set()

    for p in paths:
        path = Path(p)
        if not path.exists():
            raise DiscoveryError(f"Migration path does not exist: {path}")

        if path.is_file():
            if _is_sql_file(path):
                discovered.add(path.resolve())
        elif path.is_dir():
            for child in path.rglob("*.sql"):
                if child.is_file():
                    discovered.add(child.resolve())

    return sorted(discovered)


def discover_from_globs(patterns: list[str], root_dir: str | Path = ".") -> list[Path]:
    """Find migration files matching glob patterns relative to root_dir.

    Args:
        patterns: Glob pattern strings (e.g. 'migrations/**/*.sql').
        root_dir: Root directory for relative globs.

    Returns:
        List of matching unique .sql file Paths.
    """
    root = Path(root_dir).resolve()
    discovered: set[Path] = set()

    for pattern in patterns:
        full_pattern = str(root / pattern)
        for match in glob.glob(full_pattern, recursive=True):
            p = Path(match).resolve()
            if _is_sql_file(p):
                discovered.add(p)

    return sorted(discovered)


def discover_from_git_diff(
    base_ref: str,
    head_ref: str = "HEAD",
    root_dir: str | Path = ".",
) -> list[Path]:
    """Find migration files changed between base_ref and head_ref via git diff.

    Args:
        base_ref: Base git reference (branch, tag, or commit SHA).
        head_ref: Head git reference (default: 'HEAD').
        root_dir: Repository root directory.

    Returns:
        List of changed .sql file Paths that exist in the working tree.

    Raises:
        DiscoveryError: If git is missing or the git diff command fails.
    """
    root = Path(root_dir).resolve()
    diff_target = f"{base_ref}...{head_ref}"
    cmd = ["git", "diff", "--name-only", diff_target]

    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as err:
        raise DiscoveryError(
            "Git executable 'git' not found in system PATH. Cannot perform git diff."
        ) from err
    except Exception as err:
        raise DiscoveryError(f"Failed to execute git command: {err}") from err

    if proc.returncode != 0:
        err_msg = proc.stderr.strip() or proc.stdout.strip()
        raise DiscoveryError(
            f"Git diff failed for target '{diff_target}'. "
            f"Verify that '{base_ref}' is a valid git ref. Git error:\n{err_msg}"
        )

    discovered: set[Path] = set()
    for line in proc.stdout.splitlines():
        rel_path = line.strip()
        if not rel_path or not rel_path.lower().endswith(".sql"):
            continue

        file_path = (root / rel_path).resolve()
        # Only return files that currently exist (not deleted migrations)
        if file_path.is_file():
            discovered.add(file_path)

    return sorted(discovered)


def discover_files(
    paths: Sequence[str | Path] | None = None,
    config: Config | None = None,
    base_ref: str | None = None,
    head_ref: str = "HEAD",
    root_dir: str | Path = ".",
) -> list[Path]:
    """Unified file discovery orchestrator.

    Priority order:
    1. Explicit paths (if provided)
    2. Git diff against base_ref (if base_ref provided)
    3. Migration path globs from config (if configured)
    4. Default glob pattern (migrations/**/*.sql)
    """
    if paths:
        return discover_from_paths(paths)

    if base_ref:
        return discover_from_git_diff(base_ref, head_ref=head_ref, root_dir=root_dir)

    patterns = (
        config.migration_paths
        if config and config.migration_paths
        else ["migrations/**/*.sql", "migrations/*.sql"]
    )
    return discover_from_globs(patterns, root_dir=root_dir)
