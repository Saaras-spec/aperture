"""Rule execution engine for MigrationGuard."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from migrationguard.config import Config
from migrationguard.models import Finding, ParsedStatement, Severity, StatementContext
from migrationguard.parser import parse_sql_file, parse_sql_string
from migrationguard.rules import get_all_rules
from migrationguard.suppression import parse_suppressions


def check_statements(
    statements: list[ParsedStatement],
    config: Config,
    file_path: str = "<string>",
) -> list[Finding]:
    """Execute enabled rules across parsed statements and apply overrides and suppression.

    Args:
        statements: Parsed statements to check.
        config: MigrationGuard configuration.
        file_path: Path of the migration file.

    Returns:
        List of findings, including suppressed ones (marked suppressed=True)
        and any MG000 suppression format warnings.
    """
    all_rules = get_all_rules()
    active_rules = [r for r in all_rules if config.is_rule_enabled(r.id)]
    large_tables = {table.lower() for table in config.large_tables}

    findings: list[Finding] = []

    for idx, statement in enumerate(statements):
        supp_result = parse_suppressions(statement, file_path=file_path)
        # Any suppression errors (MG000) are included as findings
        findings.extend(supp_result.findings)

        context = StatementContext(
            file_path=file_path,
            statement_index=idx,
            all_statements=statements,
            large_tables=large_tables,
        )

        for rule in active_rules:
            rule_findings = rule.check(statement, context)
            for f in rule_findings:
                # Apply per-rule severity override from config
                f.severity = config.get_rule_severity(f.rule_id, default=f.severity)

                # Check if this rule is suppressed for this statement
                if f.rule_id in supp_result.suppressions:
                    f.suppressed = True
                    f.suppression_reason = supp_result.suppressions[f.rule_id]

                findings.append(f)

    return findings


def check_sql_string(
    sql: str,
    config: Config,
    file_path: str = "<string>",
) -> list[Finding]:
    """Parse and check raw SQL text against enabled rules."""
    statements = parse_sql_string(sql, file_path=file_path)
    return check_statements(statements, config=config, file_path=file_path)


def check_file(file_path: str | Path, config: Config) -> list[Finding]:
    """Parse and check a single SQL migration file on disk."""
    path = Path(file_path)
    statements = parse_sql_file(path)
    return check_statements(statements, config=config, file_path=str(path))


def check_files(file_paths: Sequence[str | Path], config: Config) -> list[Finding]:
    """Check multiple migration files and aggregate findings."""
    all_findings: list[Finding] = []
    for file_path in file_paths:
        file_findings = check_file(file_path, config)
        all_findings.extend(file_findings)

    # Sort findings deterministically by file, line, and rule_id
    all_findings.sort(key=lambda f: (f.file, f.line, f.rule_id))
    return all_findings


def compute_exit_code(findings: list[Finding], fail_on: Severity) -> int:
    """Compute CLI exit code based on findings and fail_on severity threshold.

    Returns:
        0: No unsuppressed findings at or above fail_on severity.
        1: One or more unsuppressed findings at or above fail_on severity.
    """
    for finding in findings:
        if not finding.suppressed and finding.severity >= fail_on:
            return 1
    return 0
