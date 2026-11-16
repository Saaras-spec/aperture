"""Snapshot-style tests for each reporter and SARIF 2.1.0 validation."""

import json

import pytest

from aperture.models import Finding, Severity
from aperture.reporters import (
    format_console,
    format_json,
    format_markdown,
    format_sarif,
    render_report,
)


@pytest.fixture
def sample_findings() -> list[Finding]:
    """Provide a consistent set of test findings."""
    return [
        Finding(
            rule_id="MG001",
            severity=Severity.ERROR,
            message="Dropping table 'users' causes data loss.",
            suggestion="Deprecate table first in app code.",
            file="migrations/001_drop.sql",
            line=5,
            statement_sql="DROP TABLE users;",
            suppressed=False,
        ),
        Finding(
            rule_id="MG002",
            severity=Severity.WARNING,
            message="Renaming column 'email' breaks running apps.",
            suggestion="Add new column, dual-write, then drop old.",
            file="migrations/002_rename.sql",
            line=12,
            statement_sql="ALTER TABLE users RENAME COLUMN email TO contact_email;",
            suppressed=True,
            suppression_reason="Backfill already complete in v2.4",
        ),
    ]


def test_console_reporter_output(sample_findings: list[Finding]) -> None:
    """Verify console reporter groups by file and formats severity and suggestions."""
    output = format_console(sample_findings)
    assert "migrations/001_drop.sql" in output
    assert "migrations/002_rename.sql" in output
    assert "MG001" in output
    assert "MG002" in output
    assert "DROP TABLE users;" in output
    assert "Deprecate table first in app code." in output
    assert "Ignored: Backfill already complete in v2.4" in output
    assert "Summary: Found" in output


def test_console_reporter_empty() -> None:
    """Verify console reporter returns green success message when findings are empty."""
    output = format_console([])
    assert "No migration risks detected" in output


def test_markdown_reporter_structure(sample_findings: list[Finding]) -> None:
    """Verify markdown reporter starts with hidden marker and contains tables and details."""
    output = format_markdown(sample_findings)
    # Must start with exact hidden marker for PR comment updater
    assert output.startswith("<!-- aperture -->")
    assert "## 🛡️ Aperture Report" in output
    assert "| Severity | Rule | Location | Message | Status |" in output
    assert "🔴 ERROR" in output
    assert "⚪ SUPPRESSED" in output
    assert "Backfill already complete in v2.4" in output
    assert "<details>" in output
    assert "```sql\nDROP TABLE users;\n```" in output


def test_markdown_reporter_empty() -> None:
    """Verify markdown report when no findings are detected."""
    output = format_markdown([])
    assert output.startswith("<!-- aperture -->")
    assert "Zero risks detected" in output


def test_json_reporter_schema(sample_findings: list[Finding]) -> None:
    """Verify json_reporter generates valid JSON including summary and suppressed findings."""
    raw_json = format_json(sample_findings)
    data = json.loads(raw_json)

    assert "summary" in data
    summary = data["summary"]
    assert summary["total"] == 2
    assert summary["errors"] == 1
    assert summary["warnings"] == 0  # 1 warning exists but is suppressed
    assert summary["suppressed"] == 1
    assert summary["passed"] is False

    assert "findings" in data
    assert len(data["findings"]) == 2
    f1 = data["findings"][0]
    assert f1["rule_id"] == "MG001"
    assert f1["severity"] == "ERROR"
    assert f1["suppressed"] is False

    f2 = data["findings"][1]
    assert f2["rule_id"] == "MG002"
    assert f2["suppressed"] is True
    assert f2["suppression_reason"] == "Backfill already complete in v2.4"


def test_sarif_reporter_validation(sample_findings: list[Finding]) -> None:
    """Validate SARIF 2.1.0 output structure against SARIF schema expectations."""
    raw_sarif = format_sarif(sample_findings)
    doc = json.loads(raw_sarif)

    # Required top-level SARIF fields
    assert doc["version"] == "2.1.0"
    assert "sarif-schema-2.1.0.json" in doc["$schema"]
    assert len(doc["runs"]) == 1

    run = doc["runs"][0]
    tool = run["tool"]["driver"]
    assert tool["name"] == "Aperture"
    assert "rules" in tool
    assert any(r["id"] == "MG001" for r in tool["rules"])

    results = run["results"]
    assert len(results) == 2

    # Check finding 1 (active)
    r1 = results[0]
    assert r1["ruleId"] == "MG001"
    assert r1["level"] == "error"
    assert r1["message"]["text"] == "Dropping table 'users' causes data loss."
    loc1 = r1["locations"][0]["physicalLocation"]
    assert loc1["artifactLocation"]["uri"] == "migrations/001_drop.sql"
    assert loc1["region"]["startLine"] == 5

    # Check finding 2 (suppressed)
    r2 = results[1]
    assert r2["ruleId"] == "MG002"
    assert r2["level"] == "warning"
    assert "suppressions" in r2
    assert r2["suppressions"][0]["kind"] == "inSource"
    assert r2["suppressions"][0]["justification"] == "Backfill already complete in v2.4"


def test_render_report_dispatch(sample_findings: list[Finding]) -> None:
    """Verify render_report dispatches correctly across all formats."""
    assert "Summary:" in render_report(sample_findings, "console")
    assert "<!-- aperture -->" in render_report(sample_findings, "markdown")
    assert '"summary":' in render_report(sample_findings, "json")
    assert '"version": "2.1.0"' in render_report(sample_findings, "sarif")

    with pytest.raises(ValueError, match="Unsupported report format"):
        render_report(sample_findings, "xml")
