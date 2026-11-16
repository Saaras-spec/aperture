"""Unit tests for suppression module."""

from aperture.models import Severity
from aperture.parser import parse_sql_string
from aperture.suppression import parse_suppressions


def test_valid_suppression_with_reason() -> None:
    """Verify single line suppression with reason is parsed correctly."""
    sql = """
    -- aperture:ignore MG001 Dropping deprecated table verified by DBA
    DROP TABLE users;
    """
    stmts = parse_sql_string(sql, file_path="001.sql")
    assert len(stmts) == 1
    res = parse_suppressions(stmts[0], file_path="001.sql")
    assert "MG001" in res.suppressions
    assert res.suppressions["MG001"] == "Dropping deprecated table verified by DBA"
    assert res.findings == []


def test_multiple_suppressions_on_same_statement() -> None:
    """Verify multiple ignore comments on the same statement are collected."""
    sql = """
    -- aperture:ignore MG001 Dropping legacy column
    -- aperture:ignore MG010 Approved lock window
    ALTER TABLE users DROP COLUMN legacy;
    """
    stmts = parse_sql_string(sql, file_path="002.sql")
    res = parse_suppressions(stmts[0], file_path="002.sql")
    assert len(res.suppressions) == 2
    assert res.suppressions["MG001"] == "Dropping legacy column"
    assert res.suppressions["MG010"] == "Approved lock window"
    assert res.findings == []


def test_block_comment_suppression() -> None:
    """Verify block comments /* ... */ with suppression are parsed."""
    sql = """
    /* aperture:ignore MG002 Renaming table with dual-read proxy in place */
    ALTER TABLE old_name RENAME TO new_name;
    """
    stmts = parse_sql_string(sql, file_path="003.sql")
    res = parse_suppressions(stmts[0], file_path="003.sql")
    assert "MG002" in res.suppressions
    assert "dual-read proxy" in res.suppressions["MG002"]


def test_missing_reason_produces_mg000_warning() -> None:
    """Verify missing reason produces an MG000 finding and does not suppress the rule."""
    sql = """
    -- aperture:ignore MG001
    DROP TABLE users;
    """
    stmts = parse_sql_string(sql, file_path="bad_supp.sql")
    res = parse_suppressions(stmts[0], file_path="bad_supp.sql")
    assert "MG001" not in res.suppressions
    assert len(res.findings) == 1
    finding = res.findings[0]
    assert finding.rule_id == "MG000"
    assert finding.severity == Severity.WARNING
    assert "missing a mandatory reason" in finding.message
    assert finding.line == stmts[0].start_line


def test_empty_ignore_comment_produces_mg000() -> None:
    """Verify ignore comment with no rule ID or reason produces MG000 finding."""
    sql = """
    -- aperture:ignore
    DROP TABLE users;
    """
    stmts = parse_sql_string(sql, file_path="empty_ignore.sql")
    res = parse_suppressions(stmts[0], file_path="empty_ignore.sql")
    assert res.suppressions == {}
    assert len(res.findings) == 1
    assert res.findings[0].rule_id == "MG000"
    assert "missing rule ID and reason" in res.findings[0].message


def test_unrelated_comments_ignored() -> None:
    """Verify regular non-suppression comments produce neither suppressions nor findings."""
    sql = """
    -- This is a regular comment
    -- Author: Alice
    /* Another regular block comment */
    CREATE TABLE users (id int);
    """
    stmts = parse_sql_string(sql, file_path="clean.sql")
    res = parse_suppressions(stmts[0], file_path="clean.sql")
    assert res.suppressions == {}
    assert res.findings == []
