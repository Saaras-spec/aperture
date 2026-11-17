"""Aperture Real-World Migration Benchmark Suite.

Discovers SQL migration files across open-source repositories, runs Aperture
static analysis with schema awareness, excludes rollback/down migrations,
measures throughput and rule findings, and outputs benchmark.json and benchmark.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from aperture.config import Config
from aperture.engine import check_statements
from aperture.models import Finding
from aperture.parser import ParseError, parse_sql_file
from aperture.rules import discover_rules
from aperture.schema import SchemaState


@dataclass
class ParseFailureInfo:
    """Record of an unparseable migration file."""

    file: str
    line: int
    error: str


@dataclass
class BenchmarkFindingInfo:
    """Summary of a finding reported during benchmark."""

    rule_id: str
    rule_name: str
    severity: str
    file: str
    line: int
    message: str
    suggestion: str
    statement_sql: str


@dataclass
class RepoBenchmarkResult:
    """Benchmark outcome for an individual repository."""

    repo_name: str
    repo_path: str
    files_scanned: int = 0
    statements_parsed: int = 0
    duration_seconds: float = 0.0
    statements_per_second: float = 0.0
    findings_by_rule: dict[str, int] = field(default_factory=dict)
    total_findings: int = 0
    parse_failures: list[ParseFailureInfo] = field(default_factory=list)
    findings: list[BenchmarkFindingInfo] = field(default_factory=list)


@dataclass
class BenchmarkSuiteResult:
    """Aggregated benchmark outcome across multiple repositories."""

    generated_at: str
    total_repos: int = 0
    total_files: int = 0
    total_statements: int = 0
    total_findings: int = 0
    total_parse_failures: int = 0
    total_duration_seconds: float = 0.0
    overall_statements_per_second: float = 0.0
    findings_by_rule: dict[str, int] = field(default_factory=dict)
    repos: list[RepoBenchmarkResult] = field(default_factory=list)


def is_rollback_or_down_migration(path: Path) -> bool:
    """Check if file is a rollback/down migration that should be excluded.

    Matches patterns:
    - *.down.sql (case-insensitive)
    - in down/ or rollback/ directories
    - filenames starting with down_ or rollback_
    """
    name_lower = path.name.lower()
    if name_lower.endswith(".down.sql"):
        return True

    if name_lower.startswith(("down_", "rollback_", "down.", "rollback.")):
        return True

    parts_lower = [p.lower() for p in path.parts]
    return any(p in {"down", "rollback", "rollbacks", "downs"} for p in parts_lower)


def discover_migration_files(repo_path: Path) -> list[Path]:
    """Find forward SQL migration files in repository in sorted order.

    Excludes rollback/down migrations, version control dirs, and test fixtures.
    """
    if not repo_path.is_dir():
        return []

    discovered: list[Path] = []
    ignored_dir_names = {
        ".git",
        ".github",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }

    for root, dirs, files in os.walk(repo_path):
        # Prune ignored directory subtrees in-place
        dirs[:] = [
            d
            for d in dirs
            if d not in ignored_dir_names
            and d.lower() not in {"down", "rollback", "rollbacks", "downs"}
        ]

        for file_name in files:
            if not file_name.lower().endswith(".sql"):
                continue

            file_path = Path(root) / file_name
            if not is_rollback_or_down_migration(file_path):
                discovered.append(file_path)

    # Sort deterministically
    return sorted(discovered, key=lambda p: (str(p.parent).lower(), p.name.lower()))


def benchmark_repo(
    repo_path: Path,
    config: Config | None = None,
) -> RepoBenchmarkResult:
    """Benchmark Aperture scanning against a single repository.

    Args:
        repo_path: Path to the root or migration directory of the cloned repo.
        config: Optional Aperture configuration (uses defaults if omitted).

    Returns:
        RepoBenchmarkResult with metrics, findings, and parse failures.
    """
    if config is None:
        config = Config()

    repo_name = repo_path.name
    files = discover_migration_files(repo_path)
    result = RepoBenchmarkResult(
        repo_name=repo_name,
        repo_path=str(repo_path),
        files_scanned=len(files),
    )

    schema = SchemaState()
    all_findings: list[Finding] = []
    start_time = time.perf_counter()

    for file_path in files:
        rel_path = file_path.relative_to(repo_path)
        try:
            statements = parse_sql_file(file_path)
        except ParseError as err:
            result.parse_failures.append(
                ParseFailureInfo(
                    file=str(rel_path).replace("\\", "/"),
                    line=err.line,
                    error=err.message,
                )
            )
            continue
        except Exception as exc:
            result.parse_failures.append(
                ParseFailureInfo(
                    file=str(rel_path).replace("\\", "/"),
                    line=1,
                    error=str(exc),
                )
            )
            continue

        result.statements_parsed += len(statements)

        # Run Aperture rule engine
        file_findings = check_statements(
            statements=statements,
            config=config,
            file_path=str(rel_path).replace("\\", "/"),
            schema=schema,
        )
        all_findings.extend(file_findings)

    duration = time.perf_counter() - start_time
    result.duration_seconds = round(duration, 4)
    result.statements_per_second = (
        round(result.statements_parsed / duration, 1) if duration > 0 else 0.0
    )

    # Tally findings
    rules_map = discover_rules()
    for f in all_findings:
        if f.suppressed:
            continue
        result.findings_by_rule[f.rule_id] = result.findings_by_rule.get(f.rule_id, 0) + 1
        rule_cls = rules_map.get(f.rule_id)
        rule_name = getattr(rule_cls, "name", f.rule_id)
        result.findings.append(
            BenchmarkFindingInfo(
                rule_id=f.rule_id,
                rule_name=str(rule_name),
                severity=f.severity.value,
                file=f.file,
                line=f.line,
                message=f.message,
                suggestion=f.suggestion,
                statement_sql=f.statement_sql,
            )
        )

    result.total_findings = len(result.findings)
    return result


def aggregate_suite_results(repo_results: Sequence[RepoBenchmarkResult]) -> BenchmarkSuiteResult:
    """Aggregate individual repository benchmark results into a suite summary."""
    suite = BenchmarkSuiteResult(
        generated_at=datetime.now(UTC).isoformat(),
        total_repos=len(repo_results),
        repos=list(repo_results),
    )

    for r in repo_results:
        suite.total_files += r.files_scanned
        suite.total_statements += r.statements_parsed
        suite.total_findings += r.total_findings
        suite.total_parse_failures += len(r.parse_failures)
        suite.total_duration_seconds += r.duration_seconds

        for rule_id, count in r.findings_by_rule.items():
            suite.findings_by_rule[rule_id] = suite.findings_by_rule.get(rule_id, 0) + count

    suite.total_duration_seconds = round(suite.total_duration_seconds, 4)
    suite.overall_statements_per_second = (
        round(suite.total_statements / suite.total_duration_seconds, 1)
        if suite.total_duration_seconds > 0
        else 0.0
    )
    return suite


def generate_markdown_report(suite: BenchmarkSuiteResult) -> str:
    """Generate a GitHub-flavored Markdown report from benchmark results."""
    lines: list[str] = [
        "# Aperture Real-World Migration Benchmark",
        "",
        f"*Generated at: {suite.generated_at}*",
        "",
        "## Executive Summary",
        "",
        "| Metric | Value |",
        "| :--- | :--- |",
        f"| **Repositories Scanned** | {suite.total_repos} |",
        f"| **Migration Files Scanned** | {suite.total_files} |",
        f"| **Statements Parsed & Analyzed** | {suite.total_statements:,} |",
        f"| **Total Findings** | {suite.total_findings} |",
        f"| **Parse Failures** | {suite.total_parse_failures} |",
        f"| **Total Duration** | {suite.total_duration_seconds:.2f} s |",
        f"| **Overall Throughput** | {suite.overall_statements_per_second:,.1f} statements/sec |",
        "",
        "## Performance by Repository",
        "",
        "| Repository | Files | Statements | Findings | Failures | Time (s) | Speed (stmts/s) |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]

    for r in suite.repos:
        lines.append(
            f"| `{r.repo_name}` | {r.files_scanned} | {r.statements_parsed:,} | "
            f"{r.total_findings} | {len(r.parse_failures)} | {r.duration_seconds:.3f} | "
            f"{r.statements_per_second:,.1f} |"
        )

    lines.extend(
        [
            "",
            "## Findings Breakdown by Rule",
            "",
        ]
    )

    # Collect all rule IDs present
    all_rule_ids = sorted(
        {rule for r in suite.repos for rule in r.findings_by_rule}
        | set(suite.findings_by_rule.keys())
    )

    if all_rule_ids:
        header = "| Repository | " + " | ".join(all_rule_ids) + " | Total |"
        sep = "| :--- | " + " | ".join([":---:"] * len(all_rule_ids)) + " | :---: |"
        lines.append(header)
        lines.append(sep)

        for r in suite.repos:
            counts = [str(r.findings_by_rule.get(rule, 0)) for rule in all_rule_ids]
            counts_str = " | ".join(counts)
            lines.append(f"| `{r.repo_name}` | {counts_str} | **{r.total_findings}** |")

        total_counts = [str(suite.findings_by_rule.get(rule, 0)) for rule in all_rule_ids]
        totals_str = " | ".join(f"**{c}**" for c in total_counts)
        lines.append(f"| **Total** | {totals_str} | **{suite.total_findings}** |")
    else:
        lines.append("*Zero findings across scanned repositories.*")

    lines.extend(
        [
            "",
            "## Parse Failures",
            "",
        ]
    )

    failures_found = False
    for r in suite.repos:
        if r.parse_failures:
            failures_found = True
            lines.append(f"### `{r.repo_name}` ({len(r.parse_failures)} failures)")
            lines.append("")
            lines.append("| File | Line | Error |")
            lines.append("| :--- | :---: | :--- |")
            for pf in r.parse_failures:
                escaped_err = pf.error.replace("|", "\\|").replace("\n", " ")
                lines.append(f"| `{pf.file}` | {pf.line} | `{escaped_err}` |")
            lines.append("")

    if not failures_found:
        lines.append("*(Zero parse failures: all migration SQL parsed cleanly with pglast)*")

    lines.append("")
    return "\n".join(lines)


def run_benchmark(
    repo_paths: list[Path],
    output_json: Path,
    output_md: Path,
    config: Config | None = None,
) -> BenchmarkSuiteResult:
    """Execute benchmark across repositories and save JSON and Markdown reports."""
    repo_results: list[RepoBenchmarkResult] = []

    for path in repo_paths:
        if not path.exists():
            print(f"Warning: Repository path '{path}' does not exist, skipping.", file=sys.stderr)
            continue
        print(f"Benchmarking repository: {path.name} ({path}) ...")
        res = benchmark_repo(path, config=config)
        print(
            f"  -> {res.files_scanned} files, {res.statements_parsed} statements, "
            f"{res.total_findings} findings, {len(res.parse_failures)} parse failures in "
            f"{res.duration_seconds:.2f}s ({res.statements_per_second:.1f} stmts/s)"
        )
        repo_results.append(res)

    suite = aggregate_suite_results(repo_results)

    # Ensure parent output directories exist
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)

    # Write JSON report
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(asdict(suite), f, indent=2)
    print(f"Wrote benchmark JSON report to: {output_json}")

    # Write Markdown report
    md_content = generate_markdown_report(suite)
    output_md.write_text(md_content, encoding="utf-8")
    print(f"Wrote benchmark Markdown report to: {output_md}")

    return suite


def main() -> None:
    """CLI entrypoint for running the benchmark."""
    parser = argparse.ArgumentParser(
        description="Run Aperture benchmark across cloned migration repositories."
    )
    parser.add_argument(
        "repos",
        nargs="+",
        type=Path,
        help="Paths to cloned open-source repository directories.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("results/benchmark.json"),
        help="Output path for JSON report (default: results/benchmark.json).",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=Path("results/benchmark.md"),
        help="Output path for Markdown report (default: results/benchmark.md).",
    )

    args = parser.parse_args()
    run_benchmark(
        repo_paths=args.repos,
        output_json=args.output_json,
        output_md=args.output_md,
    )


if __name__ == "__main__":
    main()
