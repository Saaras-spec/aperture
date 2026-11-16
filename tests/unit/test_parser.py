"""Unit tests for the SQL parser."""

from pathlib import Path

import pytest

from migrationguard.parser import ParseError, parse_sql_file, parse_sql_string


def test_parse_multiple_statements() -> None:
    """Verify parsing multiple SQL statements preserves order, lines, and content."""
    sql = """
CREATE TABLE users (id serial PRIMARY KEY);
CREATE INDEX idx_users_id ON users (id);
DROP TABLE users;
"""
    statements = parse_sql_string(sql, file_path="001.sql")
    assert len(statements) == 3

    assert statements[0].start_line == 2
    assert "CREATE TABLE users" in statements[0].sql
    assert statements[0].node is not None

    assert statements[1].start_line == 3
    assert "CREATE INDEX idx_users_id" in statements[1].sql

    assert statements[2].start_line == 4
    assert "DROP TABLE users" in statements[2].sql


def test_parse_comments() -> None:
    """Verify that comments preceding statements are extracted and start lines are accurate."""
    sql = """-- Migration for users table
-- Author: DBA
CREATE TABLE users (id int);

/* Block comment
   spanning lines */
ALTER TABLE users ADD COLUMN name text;
"""
    statements = parse_sql_string(sql, file_path="002.sql")
    assert len(statements) == 2

    assert statements[0].start_line == 3
    assert "CREATE TABLE users" in statements[0].sql
    assert len(statements[0].comments) >= 1
    assert any("Migration for users table" in c for c in statements[0].comments)

    assert statements[1].start_line == 7
    assert "ALTER TABLE users ADD COLUMN name text" in statements[1].sql
    assert len(statements[1].comments) >= 1


def test_parse_multiline_statement() -> None:
    """Verify multiline statement has correct start line and complete body."""
    sql = """
CREATE TABLE orders (
    id uuid PRIMARY KEY,
    user_id int NOT NULL,
    total numeric(10, 2) DEFAULT 0.00,
    created_at timestamptz DEFAULT now()
);
"""
    statements = parse_sql_string(sql, file_path="003.sql")
    assert len(statements) == 1
    assert statements[0].start_line == 2
    assert "CREATE TABLE orders" in statements[0].sql
    assert "created_at timestamptz DEFAULT now()" in statements[0].sql


def test_parse_syntax_error() -> None:
    """Verify syntax error raises custom ParseError with file, line, and message."""
    bad_sql = "SELECT 1;\nSELECT FROM WHERE;\nSELECT 2;"
    with pytest.raises(ParseError) as exc_info:
        parse_sql_string(bad_sql, file_path="bad_syntax.sql")

    err = exc_info.value
    assert err.file == "bad_syntax.sql"
    assert err.line == 2
    assert "syntax error" in err.message.lower()
    assert "bad_syntax.sql:2:" in str(err)


def test_parse_empty_and_whitespace() -> None:
    """Verify empty string, whitespace, and comment-only inputs return empty list."""
    assert parse_sql_string("") == []
    assert parse_sql_string("   \n\t\r\n   ") == []
    assert parse_sql_string("-- only a comment\n-- second comment\n") == []
    assert parse_sql_string("/* block comment only */") == []


def test_parse_sql_file(tmp_path: Path) -> None:
    """Verify parsing SQL from a file on disk."""
    migration_file = tmp_path / "0001_create_table.sql"
    migration_file.write_text("CREATE TABLE items (id serial PRIMARY KEY);", encoding="utf-8")

    statements = parse_sql_file(migration_file)
    assert len(statements) == 1
    assert statements[0].start_line == 1
    assert "CREATE TABLE items" in statements[0].sql


def test_parse_sql_file_not_found(tmp_path: Path) -> None:
    """Verify parse_sql_file raises FileNotFoundError for non-existent file."""
    non_existent = tmp_path / "does_not_exist.sql"
    with pytest.raises(FileNotFoundError):
        parse_sql_file(non_existent)


def test_parse_multibyte_utf8() -> None:
    """Verify line numbers remain accurate with UTF-8 multi-byte characters."""
    sql = """-- Café ☕ comment
CREATE TABLE items (id int);
-- Accented résumé with emojis 🚀
DROP TABLE items;
"""
    statements = parse_sql_string(sql, file_path="utf8.sql")
    assert len(statements) == 2
    assert statements[0].start_line == 2
    assert statements[1].start_line == 4


def test_compute_line_number_edge_cases() -> None:
    """Verify line number computation with negative and out-of-bounds offsets."""
    from migrationguard.parser import _compute_line_number

    assert _compute_line_number("line1\nline2", -5) == 1
    assert _compute_line_number("line1\nline2", 1000) == 2


def test_parser_fallback_when_split_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify fallback extraction path when pglast.split fails or returns mismatched slices."""
    import pglast

    monkeypatch.setattr(
        pglast,
        "split",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("split failed")),
    )
    sql = "SELECT 1; SELECT 2"
    statements = parse_sql_string(sql)
    assert len(statements) == 2
    assert statements[0].sql == "SELECT 1"
    assert statements[1].sql == "SELECT 2"


def test_parser_handles_comments_extraction_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify parser gracefully continues if comment extraction raises an exception."""
    import pglast

    monkeypatch.setattr(
        pglast,
        "_extract_comments",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("comments failed")),
    )
    statements = parse_sql_string("SELECT 1;")
    assert len(statements) == 1
    assert statements[0].comments == []
