"""SQL parsing functionality powered by pglast."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pglast
from pglast import parser

from aperture.models import ParsedStatement


class ParseError(Exception):
    """Raised when parsing SQL fails due to a syntax error."""

    def __init__(self, message: str, file: str, line: int, location: int | None = None) -> None:
        self.message = message
        self.file = file
        self.line = line
        self.location = location
        super().__init__(f"{file}:{line}: {message}")


def _compute_line_number(text: str, offset: int) -> int:
    """Compute 1-indexed line number for a character offset in text."""
    if offset < 0:
        return 1
    safe_offset = min(offset, len(text))
    return text[:safe_offset].count("\n") + 1


def _extract_statement_sql(
    sql: str,
    raw_stmts: list[Any],
    index: int,
    slices: tuple[slice, ...] | None,
) -> str:
    """Extract clean SQL string for a statement by index."""
    if slices is not None and len(slices) == len(raw_stmts):
        sl = slices[index]
        return sql[sl.start : sl.stop].strip()

    raw = raw_stmts[index]
    start = int(raw.stmt_location or 0)
    length = int(raw.stmt_len or 0)

    if length > 0:
        return sql[start : start + length].strip()

    # Fallback when stmt_len is 0 (trailing statement)
    if index + 1 < len(raw_stmts):
        next_start = int(raw_stmts[index + 1].stmt_location or 0)
        raw_text = sql[start:next_start]
    else:
        raw_text = sql[start:]

    return raw_text.rstrip("; \t\r\n").strip()


def parse_sql_string(sql: str, file_path: str = "<string>") -> list[ParsedStatement]:
    """Parse a SQL string into a list of ParsedStatement objects.

    Args:
        sql: Raw SQL text to parse.
        file_path: Optional file path or identifier for error reporting.

    Returns:
        List of ParsedStatement objects with AST and line numbers.

    Raises:
        ParseError: If SQL contains syntax errors.
    """
    if not sql.strip():
        return []

    try:
        raw_stmts = list(pglast.parse_sql(sql))
    except (parser.ParseError, pglast.Error, Exception) as err:
        err_msg = str(err.args[0]) if err.args else str(err)
        location: int | None = (
            err.args[1] if len(err.args) > 1 and isinstance(err.args[1], int) else None
        )
        line = _compute_line_number(sql, location) if location is not None else 1
        raise ParseError(message=err_msg, file=file_path, line=line, location=location) from err

    if not raw_stmts:
        return []

    slices: tuple[slice, ...] | None
    try:
        slices = pglast.split(sql, only_slices=True)
    except Exception:
        slices = None

    raw_comments: list[Any] = []
    try:
        raw_comments = list(pglast._extract_comments(sql))
    except Exception:
        raw_comments = []

    statements: list[ParsedStatement] = []
    for i, raw_stmt in enumerate(raw_stmts):
        start_location = int(raw_stmt.stmt_location or 0)
        start_line = _compute_line_number(sql, start_location)
        stmt_sql = _extract_statement_sql(sql, raw_stmts, i, slices)

        # Associate comments preceding this statement
        if i > 0:
            prev_loc = int(raw_stmts[i - 1].stmt_location or 0)
            prev_len = int(raw_stmts[i - 1].stmt_len or 0)
            prev_end = prev_loc + max(prev_len, 0)
        else:
            prev_end = 0

        associated_comments: list[str] = [
            str(c.text) for c in raw_comments if prev_end <= c.location <= start_location
        ]

        parsed = ParsedStatement(
            stmt=raw_stmt.stmt,
            sql=stmt_sql,
            start_line=start_line,
            start_location=start_location,
            raw_stmt=raw_stmt,
            comments=associated_comments,
        )
        statements.append(parsed)

    return statements


def parse_sql_file(file_path: str | Path) -> list[ParsedStatement]:
    """Read and parse a SQL file from disk.

    Args:
        file_path: Path to the .sql file.

    Returns:
        List of ParsedStatement objects.

    Raises:
        FileNotFoundError: If the file does not exist.
        ParseError: If SQL contains syntax errors.
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"SQL file not found: {path}")

    sql_content = path.read_text(encoding="utf-8")
    return parse_sql_string(sql_content, file_path=str(path))
