"""Unit tests for core models and severity ordering."""

import pytest

from migrationguard.models import Finding, ParsedStatement, Severity, StatementContext


def test_severity_ordering() -> None:
    """Verify Severity enum follows INFO < WARNING < ERROR ordering."""
    assert Severity.INFO < Severity.WARNING
    assert Severity.WARNING < Severity.ERROR
    assert Severity.INFO < Severity.ERROR

    assert Severity.ERROR > Severity.WARNING
    assert Severity.WARNING > Severity.INFO

    assert Severity.ERROR >= Severity.ERROR
    assert Severity.WARNING >= Severity.INFO
    assert not (Severity.WARNING >= Severity.ERROR)

    assert Severity.ERROR == Severity.ERROR
    assert Severity.ERROR == "ERROR"
    assert Severity.WARNING == "WARNING"
    assert Severity.INFO == "INFO"

    # Non-Severity comparisons raise TypeError
    with pytest.raises(TypeError):
        _ = Severity.INFO < 10
    with pytest.raises(TypeError):
        _ = Severity.INFO <= 10
    with pytest.raises(TypeError):
        _ = Severity.INFO > 10
    with pytest.raises(TypeError):
        _ = Severity.INFO >= 10

    # Hashable in sets and dicts
    sev_set = {Severity.INFO, Severity.WARNING, Severity.ERROR}
    assert len(sev_set) == 3


def test_finding_dataclass() -> None:
    """Verify Finding stores all attributes and handles default suppression fields."""
    finding = Finding(
        rule_id="MG001",
        severity=Severity.ERROR,
        message="DROP COLUMN is risky",
        suggestion="Mark column as deprecated first",
        file="migrations/001.sql",
        line=10,
        statement_sql="ALTER TABLE users DROP COLUMN email;",
    )
    assert finding.rule_id == "MG001"
    assert finding.severity == Severity.ERROR
    assert finding.suppressed is False
    assert finding.suppression_reason is None

    finding_suppressed = Finding(
        rule_id="MG001",
        severity=Severity.ERROR,
        message="DROP COLUMN is risky",
        suggestion="Mark column as deprecated first",
        file="migrations/001.sql",
        line=10,
        statement_sql="ALTER TABLE users DROP COLUMN email;",
        suppressed=True,
        suppression_reason="Intentional cleanup after deprecation window",
    )
    assert finding_suppressed.suppressed is True
    assert finding_suppressed.suppression_reason == "Intentional cleanup after deprecation window"


def test_parsed_statement_aliases() -> None:
    """Verify ParsedStatement properties node and statement_sql."""
    stmt = ParsedStatement(
        stmt={"dummy": "ast"},
        sql="SELECT 1;",
        start_line=1,
        start_location=0,
        comments=["-- initial query"],
    )
    assert stmt.node == {"dummy": "ast"}
    assert stmt.statement_sql == "SELECT 1;"
    assert stmt.comments == ["-- initial query"]


def test_statement_context_defaults() -> None:
    """Verify StatementContext default values."""
    ctx = StatementContext(file_path="migrations/001.sql")
    assert ctx.file_path == "migrations/001.sql"
    assert ctx.statement_index == 0
    assert ctx.all_statements == []
    assert ctx.large_tables == set()
