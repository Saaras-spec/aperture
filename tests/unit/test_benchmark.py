"""Unit tests for scripts/benchmark.py."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.benchmark import (
    BenchmarkFindingInfo,
    ParseFailureInfo,
    RepoBenchmarkResult,
    aggregate_suite_results,
    benchmark_repo,
    discover_migration_files,
    generate_markdown_report,
    is_rollback_or_down_migration,
    run_benchmark,
)


def test_is_rollback_or_down_migration() -> None:
    """Test detection and exclusion of rollback/down migrations."""
    assert is_rollback_or_down_migration(Path("001_create.down.sql")) is True
    assert is_rollback_or_down_migration(Path("DOWN_001.sql")) is True
    assert is_rollback_or_down_migration(Path("rollback_table.sql")) is True
    assert is_rollback_or_down_migration(Path("migrations/down/001.sql")) is True
    assert is_rollback_or_down_migration(Path("migrations/rollback/001.sql")) is True
    assert is_rollback_or_down_migration(Path("migrations/rollbacks/001.sql")) is True

    # Forward migrations must not be marked as rollback
    assert is_rollback_or_down_migration(Path("001_create.up.sql")) is False
    assert is_rollback_or_down_migration(Path("001_create.sql")) is False
    assert is_rollback_or_down_migration(Path("migrations/up/001.sql")) is False
    assert is_rollback_or_down_migration(Path("migrations/001_initial.sql")) is False


def test_discover_migration_files(tmp_path: Path) -> None:
    """Test discovery excludes down migrations and hidden system directories."""
    repo = tmp_path / "test_repo"
    migs = repo / "migrations"
    migs.mkdir(parents=True)

    up1 = migs / "001_users.up.sql"
    up1.write_text("CREATE TABLE users (id int);", encoding="utf-8")
    down1 = migs / "001_users.down.sql"
    down1.write_text("DROP TABLE users;", encoding="utf-8")

    down_dir = migs / "down"
    down_dir.mkdir()
    down2 = down_dir / "002.sql"
    down2.write_text("DROP TABLE orders;", encoding="utf-8")

    git_dir = repo / ".git"
    git_dir.mkdir()
    (git_dir / "ignored.sql").write_text("SELECT 1;", encoding="utf-8")

    discovered = discover_migration_files(repo)
    assert len(discovered) == 1
    assert discovered[0].name == "001_users.up.sql"

    # Non-existent directory returns empty list
    assert discover_migration_files(tmp_path / "nonexistent") == []


def test_benchmark_repo_with_findings_and_parse_failure(tmp_path: Path) -> None:
    """benchmark_repo parses statements, flags risks, and records parse failures."""
    repo = tmp_path / "sample_repo"
    migs = repo / "db" / "migrations"
    migs.mkdir(parents=True)

    # Valid SQL with MG005 finding (CREATE INDEX without CONCURRENTLY)
    valid_sql = migs / "001_index.sql"
    valid_sql.write_text("CREATE INDEX idx_users ON users (email);", encoding="utf-8")

    # Invalid SQL syntax
    bad_sql = migs / "002_bad.sql"
    bad_sql.write_text("THIS IS INVALID SQL STATEMENT @@;", encoding="utf-8")

    res = benchmark_repo(repo)
    assert res.repo_name == "sample_repo"
    assert res.files_scanned == 2
    assert res.statements_parsed == 1
    assert res.findings_by_rule.get("MG005") == 1
    assert res.total_findings == 1
    assert len(res.parse_failures) == 1
    assert res.parse_failures[0].file.endswith("002_bad.sql")
    assert "syntax error" in res.parse_failures[0].error.lower()
    assert res.statements_per_second >= 0.0


def test_aggregate_suite_results_and_markdown_generation() -> None:
    """aggregate_suite_results aggregates stats and generate_markdown_report renders cleanly."""
    r1 = RepoBenchmarkResult(
        repo_name="repo1",
        repo_path="/path/to/repo1",
        files_scanned=10,
        statements_parsed=50,
        duration_seconds=0.5,
        statements_per_second=100.0,
        findings_by_rule={"MG005": 2, "MG008": 1},
        total_findings=3,
        parse_failures=[ParseFailureInfo(file="005.sql", line=12, error="syntax error")],
        findings=[
            BenchmarkFindingInfo(
                rule_id="MG005",
                rule_name="CREATE INDEX without CONCURRENTLY",
                severity="ERROR",
                file="001.sql",
                line=3,
                message="Index without concurrently",
                suggestion="Add CONCURRENTLY",
                statement_sql="CREATE INDEX idx ON t (c);",
            )
        ],
    )

    r2 = RepoBenchmarkResult(
        repo_name="repo2",
        repo_path="/path/to/repo2",
        files_scanned=5,
        statements_parsed=20,
        duration_seconds=0.2,
        statements_per_second=100.0,
        findings_by_rule={"MG005": 1},
        total_findings=1,
    )

    suite = aggregate_suite_results([r1, r2])
    assert suite.total_repos == 2
    assert suite.total_files == 15
    assert suite.total_statements == 70
    assert suite.total_findings == 4
    assert suite.total_parse_failures == 1
    assert suite.findings_by_rule["MG005"] == 3
    assert suite.findings_by_rule["MG008"] == 1

    md = generate_markdown_report(suite)
    assert "# Aperture Real-World Migration Benchmark" in md
    assert "`repo1`" in md
    assert "`repo2`" in md
    assert "MG005" in md
    assert "MG008" in md
    assert "syntax error" in md


def test_run_benchmark_writes_json_and_md(tmp_path: Path) -> None:
    """run_benchmark processes multiple repos and writes output files."""
    repo = tmp_path / "repo_a"
    repo.mkdir()
    mig = repo / "001.sql"
    mig.write_text("CREATE TABLE accounts (id int);", encoding="utf-8")

    out_json = tmp_path / "results" / "benchmark.json"
    out_md = tmp_path / "results" / "benchmark.md"

    suite = run_benchmark(
        repo_paths=[repo, tmp_path / "nonexistent"],
        output_json=out_json,
        output_md=out_md,
    )

    assert suite.total_repos == 1
    assert suite.total_files == 1
    assert suite.total_statements == 1
    assert out_json.is_file()
    assert out_md.is_file()

    # Validate JSON structure
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["total_repos"] == 1
    assert data["total_statements"] == 1
    assert len(data["repos"]) == 1
    assert data["repos"][0]["repo_name"] == "repo_a"
