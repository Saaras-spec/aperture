"""AST-based automated safe migration rewrites for Aperture."""

from __future__ import annotations

import difflib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pglast
from pglast import enums, stream

from aperture.config import Config
from aperture.engine import check_statements
from aperture.models import ParsedStatement
from aperture.parser import parse_sql_string
from aperture.rules.helpers import get_table_name
from aperture.schema import SchemaState


@dataclass
class FixResult:
    """Result of running auto-fix on a migration file."""

    file: str
    original: str
    rewritten: str
    diff: str
    modified: bool
    written: bool
    skipped_explanations: list[str] = field(default_factory=list)


def get_statement_span(
    sql: str,
    raw_stmts: list[Any],
    slices: tuple[slice, ...] | None,
    index: int,
) -> tuple[int, int]:
    """Calculate the start and end character offsets of a statement including trailing semicolon."""
    if slices is not None and len(slices) == len(raw_stmts):
        start = slices[index].start
        stop = slices[index].stop
    else:
        raw = raw_stmts[index]
        start = int(raw.stmt_location or 0)
        length = int(raw.stmt_len or 0)
        stop = start + length if length > 0 else len(sql)

    end = stop
    while end < len(sql) and sql[end] in (" ", "\t", "\r"):
        end += 1
    if end < len(sql) and sql[end] == ";":
        end += 1

    return start, end


def detect_transaction_blocks(statements: list[ParsedStatement]) -> list[bool]:
    """Determine for each statement whether it is inside a BEGIN...COMMIT transaction block."""
    in_txn = False
    in_txn_map: list[bool] = []
    for ps in statements:
        stmt_node = ps.stmt
        if type(stmt_node).__name__ == "TransactionStmt":
            kind = getattr(stmt_node, "kind", None)
            if kind in (
                enums.TransactionStmtKind.TRANS_STMT_BEGIN,
                enums.TransactionStmtKind.TRANS_STMT_START,
            ):
                in_txn = True
            elif kind in (
                enums.TransactionStmtKind.TRANS_STMT_COMMIT,
                enums.TransactionStmtKind.TRANS_STMT_ROLLBACK,
                enums.TransactionStmtKind.TRANS_STMT_COMMIT_PREPARED,
                enums.TransactionStmtKind.TRANS_STMT_ROLLBACK_PREPARED,
            ):
                in_txn = False
        in_txn_map.append(in_txn)
    return in_txn_map


def _rewrite_mg005(stmt_node: Any) -> str | None:
    """Rewrite CREATE INDEX to CREATE INDEX CONCURRENTLY."""
    if type(stmt_node).__name__ == "IndexStmt":
        stmt_node.concurrent = True
        return str(stream.RawStream()(stmt_node)) + ";"
    return None


def _rewrite_mg006_mg007(
    stmt_node: Any,
    rule_id: str,
    file_path: str,
    line: int,
    skipped: list[str],
) -> str | None:
    """Rewrite ADD FOREIGN KEY / CHECK constraint to NOT VALID and append VALIDATE CONSTRAINT."""
    if type(stmt_node).__name__ != "AlterTableStmt":
        return None

    cmds = getattr(stmt_node, "cmds", None) or ()
    target_contype = (
        enums.ConstrType.CONSTR_FOREIGN if rule_id == "MG006" else enums.ConstrType.CONSTR_CHECK
    )

    for cmd in cmds:
        if getattr(cmd, "subtype", None) == enums.AlterTableType.AT_AddConstraint:
            c_def = getattr(cmd, "def_", None)
            if c_def and getattr(c_def, "contype", None) == target_contype:
                conname = getattr(c_def, "conname", None)
                if not conname:
                    skipped.append(
                        f"{file_path}:{line}: Cannot auto-fix anonymous constraint "
                        f"for {rule_id}: explicit constraint name required."
                    )
                    return None

                c_def.skip_validation = True
                tbl = get_table_name(stmt_node)
                if not tbl and hasattr(stmt_node, "relation") and stmt_node.relation:
                    tbl = str(stream.RawStream()(stmt_node.relation))
                tbl_str = tbl or "table"
                rewritten_stmt = str(stream.RawStream()(stmt_node))
                validate_stmt = f"ALTER TABLE {tbl_str} VALIDATE CONSTRAINT {conname};"
                return f"{rewritten_stmt};\n{validate_stmt}"
    return None


def fix_sql_string(
    sql: str,
    file_path: str = "<string>",
    config: Config | None = None,
    schema: SchemaState | None = None,
) -> FixResult:
    """Analyze SQL string and apply AST-based safe rewrites."""
    if config is None:
        config = Config()
    if schema is None:
        schema = SchemaState()

    original = sql
    if not sql.strip():
        return FixResult(
            file=file_path,
            original=original,
            rewritten=original,
            diff="",
            modified=False,
            written=False,
        )

    statements = parse_sql_string(sql, file_path=file_path)
    if not statements:
        return FixResult(
            file=file_path,
            original=original,
            rewritten=original,
            diff="",
            modified=False,
            written=False,
        )

    findings = check_statements(statements, config=config, file_path=file_path, schema=schema)
    in_txn_map = detect_transaction_blocks(statements)

    raw_stmts: list[Any] = []
    try:
        raw_stmts = list(pglast.parse_sql(sql))
    except Exception:
        raw_stmts = []

    slices: tuple[slice, ...] | None = None
    try:
        slices = pglast.split(sql, only_slices=True)
    except Exception:
        slices = None

    replacements: dict[int, str] = {}
    skipped_explanations: list[str] = []
    needs_lock_timeout = False

    for f in findings:
        if f.suppressed or f.baselined:
            continue

        idx = f.statement_index
        if idx is None or idx >= len(statements):
            continue

        ps = statements[idx]

        if f.rule_id == "MG005":
            if in_txn_map[idx]:
                skipped_explanations.append(
                    f"{file_path}:{ps.start_line}: Cannot auto-fix MG005 because "
                    "CREATE INDEX CONCURRENTLY cannot run inside a transaction block "
                    "(BEGIN...COMMIT). Move the statement outside the transaction block."
                )
            else:
                rep = _rewrite_mg005(ps.stmt)
                if rep:
                    replacements[idx] = rep

        elif f.rule_id in ("MG006", "MG007"):
            rep = _rewrite_mg006_mg007(
                ps.stmt,
                f.rule_id,
                file_path,
                ps.start_line,
                skipped_explanations,
            )
            if rep:
                replacements[idx] = rep

        elif f.rule_id == "MG010":
            needs_lock_timeout = True

    # Apply statement-level replacements in reverse order of index
    rewritten = sql
    for idx in sorted(replacements.keys(), reverse=True):
        start, end = get_statement_span(sql, raw_stmts, slices, idx)
        rewritten = rewritten[:start] + replacements[idx] + rewritten[end:]

    # Apply MG010 fix (insert SET lock_timeout = '2s'; at top of file)
    if needs_lock_timeout and not re.search(r"(?i)^\s*SET\s+lock_timeout\b", rewritten):
        rewritten = "SET lock_timeout = '2s';\n\n" + rewritten

    modified = rewritten != original
    diff = ""
    if modified:
        filename = Path(file_path).name if file_path != "<string>" else "migration.sql"
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                rewritten.splitlines(keepends=True),
                fromfile=f"a/{filename}",
                tofile=f"b/{filename}",
            )
        )

    return FixResult(
        file=file_path,
        original=original,
        rewritten=rewritten,
        diff=diff,
        modified=modified,
        written=False,
        skipped_explanations=skipped_explanations,
    )


def fix_file(
    file_path: str | Path,
    write: bool = False,
    config: Config | None = None,
    schema: SchemaState | None = None,
) -> FixResult:
    """Analyze and fix a single SQL migration file."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Migration file not found: {path}")

    sql = path.read_text(encoding="utf-8")
    result = fix_sql_string(
        sql=sql,
        file_path=str(path),
        config=config,
        schema=schema,
    )
    if write and result.modified:
        path.write_text(result.rewritten, encoding="utf-8")
        result.written = True

    return result


def fix_files(
    file_paths: Sequence[str | Path],
    write: bool = False,
    config: Config | None = None,
) -> list[FixResult]:
    """Analyze and fix multiple migration files in temporal order."""
    if config is None:
        config = Config()

    sorted_paths = sorted(file_paths, key=lambda p: Path(p).name)
    schema = SchemaState()
    results: list[FixResult] = []

    for path in sorted_paths:
        res = fix_file(path, write=write, config=config, schema=schema)
        results.append(res)

    return results
