"""Dynamic migration verification against live PostgreSQL databases.

Applies migration statements inside a transactional session on a target
database (intended for throwaway/ephemeral instances), inspecting locks
via pg_locks and table rewrites via pg_class.relfilenode, then rolling back.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pglast import enums, visitors

from aperture.config import Config
from aperture.engine import check_statements
from aperture.models import Finding, ParsedStatement
from aperture.parser import parse_sql_file
from aperture.schema import SchemaState

# PostgreSQL documentation citations for lock modes and hierarchy:
# - https://www.postgresql.org/docs/current/explicit-locking.html (Section 13.3.1 Table-level Locks)
# - https://www.postgresql.org/docs/current/view-pg-locks.html (Section 54.12 pg_locks)
# - https://www.postgresql.org/docs/current/catalog-pg-class.html (relfilenode storage files)
# - https://www.postgresql.org/docs/current/functions-admin.html (pg_relation_filenode)
#
# Table-level lock mode hierarchy in PostgreSQL (1 to 8 in increasing strength):
LOCK_STRENGTH: dict[str, int] = {
    "AccessShareLock": 1,
    "RowShareLock": 2,
    "RowExclusiveLock": 3,
    "ShareUpdateExclusiveLock": 4,
    "ShareLock": 5,
    "ShareRowExclusiveLock": 6,
    "ExclusiveLock": 7,
    "AccessExclusiveLock": 8,
}


def mask_dsn(dsn: str) -> str:
    """Mask the password component in a PostgreSQL connection URI or error message.

    Args:
        dsn: PostgreSQL connection string, URI, or error message containing credentials.

    Returns:
        Sanitized string with credentials safely masked.
    """
    # 1. Mask URI style credentials: ://user:password@ -> ://user:****@
    masked = re.sub(r"://([^:@\s]+):([^@\s]+)@", r"://\1:****@", dsn)
    # 2. Mask key-value style credentials: password=secret -> password=****
    masked = re.sub(
        r"(password\s*=\s*)(['\"]?[^\s'\"]+['\"]?)",
        r"\1****",
        masked,
        flags=re.IGNORECASE,
    )
    # 3. Mask query parameter credentials: ?password=secret or &password=secret
    masked = re.sub(r"([?&]password=)([^&#\s]+)", r"\1****", masked, flags=re.IGNORECASE)
    return masked


def get_database_name(dsn: str) -> str:
    """Extract database name from a PostgreSQL connection URI or key-value string."""
    if "://" in dsn:
        try:
            parts = urlsplit(dsn)
            path = parts.path.lstrip("/")
            if path:
                return path.split("?")[0]
        except Exception:
            pass

    # Key-value style
    match = re.search(r"\bdbname\s*=\s*['\"]?([^\s'\"]+)['\"]?", dsn, flags=re.IGNORECASE)
    if match:
        return match.group(1)

    return "unknown_database"


def get_dsn_host(dsn: str) -> str:
    """Extract hostname from a PostgreSQL connection URI or key-value string."""
    if "://" in dsn:
        try:
            parts = urlsplit(dsn)
            return parts.hostname or ""
        except Exception:
            pass

    match = re.search(r"\bhost\s*=\s*['\"]?([^\s'\"]+)['\"]?", dsn, flags=re.IGNORECASE)
    if match:
        return match.group(1)

    return ""


def is_local_host(dsn: str) -> bool:
    """Check if the DSN targets a local database host.

    Local hosts include localhost, 127.0.0.1, ::1, or local Unix sockets.
    """
    host = get_dsn_host(dsn).lower().strip()
    if not host or host.startswith("/"):
        # Empty host or unix socket path is local
        return True
    return host in {"localhost", "127.0.0.1", "::1"}


class _TableFinder(visitors.Visitor):
    """Visitor that extracts all relation names referenced in an AST statement."""

    def __init__(self) -> None:
        self.tables: set[str] = set()

    def visit_RangeVar(self, _ancestors: Any, node: Any) -> None:  # noqa: N802
        relname = getattr(node, "relname", None)
        if relname:
            self.tables.add(str(relname).lower())

    def visit_DropStmt(self, _ancestors: Any, node: Any) -> None:  # noqa: N802
        if getattr(node, "removeType", None) == enums.ObjectType.OBJECT_TABLE:
            for obj in getattr(node, "objects", ()) or ():
                if isinstance(obj, (tuple, list)) and obj and hasattr(obj[-1], "sval"):
                    self.tables.add(str(obj[-1].sval).lower())


def extract_referenced_tables(stmt: Any) -> set[str]:
    """Extract lowercased names of all tables explicitly referenced in a statement AST."""
    finder = _TableFinder()
    with contextlib.suppress(Exception):
        finder(stmt)
    return finder.tables


@dataclass
class StatementLock:
    """A relation lock recorded from pg_locks during statement execution."""

    table_name: str
    mode: str
    strength: int


@dataclass
class StatementVerification:
    """Verification outcome for an individual migration statement."""

    statement_index: int
    sql: str
    line: int
    file: str
    non_transactional: bool = False
    status: str = "COMPLETED"  # COMPLETED | NOT_VERIFIABLE_IN_TRANSACTION | FAILED
    error_message: str | None = None
    locks_acquired: list[StatementLock] = field(default_factory=list)
    strongest_locks: dict[str, str] = field(default_factory=dict)
    table_rewrites: dict[str, bool] = field(default_factory=dict)
    static_findings: list[Finding] = field(default_factory=list)
    confirmations: list[str] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)


@dataclass
class VerificationReport:
    """Overall report aggregating dynamic verification results across statements."""

    database_name: str
    server_version: str
    statements: list[StatementVerification] = field(default_factory=list)
    total_statements: int = 0
    total_confirmed: int = 0
    total_mismatches: int = 0
    total_non_transactional: int = 0


def is_non_transactional_statement(statement: ParsedStatement) -> bool:
    """Check if statement cannot run inside a PostgreSQL transaction block.

    PostgreSQL documentation explicitly prohibits certain DDL commands inside
    an open transaction block:
    - CREATE INDEX CONCURRENTLY
    - DROP INDEX CONCURRENTLY
    - REINDEX CONCURRENTLY
    - VACUUM
    """
    stmt_node: Any = statement.stmt
    node_type = type(stmt_node).__name__

    if node_type == "IndexStmt" and bool(getattr(stmt_node, "concurrent", False)):
        return True

    if node_type == "DropStmt" and bool(getattr(stmt_node, "concurrent", False)):
        return True

    if node_type == "VacuumStmt":
        return True

    sql_upper = statement.sql.upper()
    return bool(re.search(r"\b(CREATE|DROP|REINDEX)\s+INDEX\s+CONCURRENTLY\b", sql_upper))


def evaluate_statement_comparison(
    sv: StatementVerification,
    static_findings: Sequence[Finding],
) -> None:
    """Compare observed PostgreSQL dynamic behaviour against static rule predictions."""
    sv.static_findings = list(static_findings)

    for f in static_findings:
        if f.suppressed:
            continue

        if f.rule_id == "MG005":
            # MG005 predicts blocking writes via ShareLock on non-concurrent index build
            found_blocking = False
            for tbl, strongest in sv.strongest_locks.items():
                strength = LOCK_STRENGTH.get(strongest, 0)
                # ShareLock (strength 5) or higher blocks writes
                if strength >= LOCK_STRENGTH["ShareLock"]:
                    sv.confirmations.append(
                        f"MG005 on '{tbl}' predicted blocking writes: CONFIRMED ({strongest} held)"
                    )
                    found_blocking = True
                    break

            if not found_blocking and not sv.non_transactional:
                sv.mismatches.append(
                    "MG005 predicted blocking writes: MISMATCH "
                    f"(observed lock strength < ShareLock: {sv.strongest_locks})"
                )

        elif f.rule_id == "MG008":
            # MG008 predicts full table rewrite on ALTER COLUMN TYPE
            rewritten_tables = [t for t, r in sv.table_rewrites.items() if r]
            if rewritten_tables:
                for tbl in rewritten_tables:
                    sv.confirmations.append(
                        f"MG008 on '{tbl}' predicted a rewrite: CONFIRMED (relfilenode changed)"
                    )
            else:
                sv.mismatches.append(
                    "MG008 predicted a rewrite: MISMATCH (relfilenode unchanged - metadata only)"
                )

        elif f.rule_id == "MG003":
            # MG003: ADD COLUMN NOT NULL without DEFAULT rewrites on non-empty table
            rewritten_tables = [t for t, r in sv.table_rewrites.items() if r]
            if rewritten_tables:
                for tbl in rewritten_tables:
                    sv.confirmations.append(
                        f"MG003 on '{tbl}' predicted a rewrite: CONFIRMED (relfilenode changed)"
                    )
            else:
                # If table had no rows or PG 11+ metadata default optimization applied
                sv.confirmations.append(
                    "MG003 table rewrite skipped or table empty: (relfilenode unchanged)"
                )


def format_verification_report(report: VerificationReport) -> str:
    """Render a human-readable console report of dynamic verification outcomes."""
    lines: list[str] = [
        "══════════════════════════════════════════════════════════════════",
        " 🔬 Aperture Dynamic Migration Verification Report",
        "══════════════════════════════════════════════════════════════════",
        f"Database:       {report.database_name}",
        f"Server Version: {report.server_version}",
        f"Total Stmts:    {report.total_statements}",
        f"Confirmed:      {report.total_confirmed}",
        f"Mismatches:     {report.total_mismatches}",
        "──────────────────────────────────────────────────────────────────",
    ]

    for sv in report.statements:
        status_badge = (
            "[OK]"
            if sv.status == "COMPLETED"
            else "[SKIPPED: NON-TRANSACTIONAL]"
            if sv.status == "NOT_VERIFIABLE_IN_TRANSACTION"
            else "[ERROR]"
        )
        lines.append(f"\n{status_badge} line {sv.line} in {Path(sv.file).name}: {sv.sql[:70]}...")

        if sv.status == "FAILED":
            lines.append(f"   ❌ Execution failed: {sv.error_message}")
            continue

        if sv.non_transactional:
            lines.append("   ⚠️  Statement cannot run inside a transaction block (CONCURRENTLY).")
            lines.append("       Skipped dynamic execution to prevent aborting verify transaction.")
            continue

        if sv.strongest_locks:
            lock_summary = ", ".join(f"{t}: {m}" for t, m in sorted(sv.strongest_locks.items()))
            lines.append(f"   🔒 Strongest Relation Locks: {lock_summary}")

        if sv.table_rewrites:
            rewrite_summary = ", ".join(
                f"{t}: {'REWRITTEN' if r else 'no rewrite'}"
                for t, r in sorted(sv.table_rewrites.items())
            )
            lines.append(f"   💾 Table Filenodes: {rewrite_summary}")

        for c in sv.confirmations:
            lines.append(f"   ✅ {c}")

        for m in sv.mismatches:
            lines.append(f"   ❌ {m}")

    if report.total_mismatches > 0:
        lines.append("\n──────────────────────────────────────────────────────────────────")
        lines.append("⚠️  Static vs. Dynamic Mismatches Detected:")
        for sv in report.statements:
            for m in sv.mismatches:
                lines.append(f"  • Line {sv.line}: {m}")

    lines.append("\n══════════════════════════════════════════════════════════════════\n")
    return "\n".join(lines)


def run_dynamic_verification(
    statements: list[ParsedStatement],
    dsn: str,
    file_path: str = "<string>",
    config: Config | None = None,
    schema: SchemaState | None = None,
) -> VerificationReport:
    """Execute migration statements inside a rolled-back transaction on live PostgreSQL."""
    try:
        import psycopg
    except ImportError as err:
        raise RuntimeError(
            "psycopg is required for dynamic verification. "
            "Install it via 'pip install psycopg[binary]'."
        ) from err

    if config is None:
        config = Config()
    if schema is None:
        schema = SchemaState()

    db_name = get_database_name(dsn)
    report = VerificationReport(
        database_name=db_name,
        server_version="unknown",
        total_statements=len(statements),
    )

    # Pre-compute static findings
    static_findings = check_statements(
        statements,
        config=config,
        file_path=file_path,
        schema=schema,
    )
    findings_by_idx: dict[int, list[Finding]] = {}
    for f in static_findings:
        if f.statement_index is not None:
            findings_by_idx.setdefault(f.statement_index, []).append(f)

    # Establish connection with autocommit=False to manage a single transaction block
    with psycopg.connect(dsn, autocommit=False) as conn:
        server_ver = str(conn.info.server_version)
        report.server_version = server_ver

        with conn.cursor() as cur:
            # Query backend pid for pg_locks filtering
            cur.execute("SELECT pg_backend_pid();")
            pid_row = cur.fetchone()
            backend_pid = pid_row[0] if pid_row else None

            for idx, ps in enumerate(statements):
                sv = StatementVerification(
                    statement_index=idx,
                    sql=ps.sql,
                    line=ps.start_line,
                    file=file_path,
                )

                if is_non_transactional_statement(ps):
                    sv.non_transactional = True
                    sv.status = "NOT_VERIFIABLE_IN_TRANSACTION"
                    report.total_non_transactional += 1
                    evaluate_statement_comparison(sv, findings_by_idx.get(idx, []))
                    report.statements.append(sv)
                    continue

                # 1. Snapshot table relfilenodes and relation locks before statement
                # We filter to user tables in public schema
                cur.execute(
                    """
                    SELECT c.relname, pg_relation_filenode(c.oid)
                    FROM pg_class c
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
                      AND c.relkind = 'r';
                    """
                )
                before_filenodes: dict[str, int | None] = dict(cur.fetchall())

                cur.execute(
                    """
                    SELECT c.relname, l.mode
                    FROM pg_locks l
                    JOIN pg_class c ON l.relation = c.oid
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    WHERE l.pid = %s
                      AND l.locktype = 'relation'
                      AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast');
                    """,
                    (backend_pid,),
                )
                before_locks: set[tuple[str, str]] = set(cur.fetchall())

                # 2. Execute statement inside the active transaction
                try:
                    cur.execute(ps.sql)
                except Exception as exc:
                    sv.status = "FAILED"
                    sv.error_message = str(exc)
                    report.statements.append(sv)
                    # Abort transaction on SQL failure
                    conn.rollback()
                    break

                # 3. Snapshot table relfilenodes after statement to detect rewrites
                cur.execute(
                    """
                    SELECT c.relname, pg_relation_filenode(c.oid)
                    FROM pg_class c
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
                      AND c.relkind = 'r';
                    """
                )
                after_filenodes: dict[str, int | None] = dict(cur.fetchall())

                ref_tables = extract_referenced_tables(ps.stmt)
                for tbl, aft_fn in after_filenodes.items():
                    if tbl in before_filenodes:
                        bef_fn = before_filenodes[tbl]
                        # A changed filenode proves the table storage file was rewritten
                        if bef_fn != aft_fn:
                            sv.table_rewrites[tbl] = True
                        elif tbl.lower() in ref_tables:
                            sv.table_rewrites[tbl] = False

                # 4. Query pg_locks for relation locks held by this backend
                cur.execute(
                    """
                    SELECT c.relname, l.mode
                    FROM pg_locks l
                    JOIN pg_class c ON l.relation = c.oid
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    WHERE l.pid = %s
                      AND l.locktype = 'relation'
                      AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast');
                    """,
                    (backend_pid,),
                )
                raw_locks = cur.fetchall()

                # Attribute locks per-statement:
                # Include locks newly acquired by this statement, or locks on tables
                # explicitly referenced in this statement AST (e.g. successive statements
                # targeting the same table). Exclude leftover locks from earlier statements
                # on unrelated tables.
                locks_by_table: dict[str, list[str]] = {}
                for tbl, mode in raw_locks:
                    tbl_lower = tbl.lower()
                    is_new = (tbl, mode) not in before_locks
                    is_referenced = tbl_lower in ref_tables
                    if is_new or is_referenced:
                        strength = LOCK_STRENGTH.get(mode, 0)
                        lock_obj = StatementLock(table_name=tbl, mode=mode, strength=strength)
                        sv.locks_acquired.append(lock_obj)
                        locks_by_table.setdefault(tbl, []).append(mode)

                for tbl, modes in locks_by_table.items():
                    strongest = max(modes, key=lambda m: LOCK_STRENGTH.get(m, 0))
                    sv.strongest_locks[tbl] = strongest

                # 5. Evaluate dynamic observations against static rule findings
                evaluate_statement_comparison(sv, findings_by_idx.get(idx, []))
                report.total_confirmed += len(sv.confirmations)
                report.total_mismatches += len(sv.mismatches)
                report.statements.append(sv)

            # 6. Always roll back at the end of verification
            conn.rollback()

    return report


def verify_file(
    file_path: str | Path,
    dsn: str,
    config: Config | None = None,
    schema: SchemaState | None = None,
    allow_remote: bool = False,
) -> VerificationReport:
    """Run dynamic verification for a single migration file on disk."""
    if not is_local_host(dsn) and not allow_remote:
        raise ValueError(
            f"Refusing to run dynamic verification against non-local host '{get_dsn_host(dsn)}'. "
            "Pass allow_remote=True or --allow-remote to permit remote execution."
        )

    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Migration file not found: {path}")

    statements = parse_sql_file(path)
    return run_dynamic_verification(
        statements=statements,
        dsn=dsn,
        file_path=str(path),
        config=config,
        schema=schema,
    )


def verify_files(
    file_paths: Sequence[str | Path],
    dsn: str,
    config: Config | None = None,
    allow_remote: bool = False,
) -> VerificationReport:
    """Run dynamic verification across multiple migration files in temporal order."""
    if not is_local_host(dsn) and not allow_remote:
        raise ValueError(
            f"Refusing to run dynamic verification against non-local host '{get_dsn_host(dsn)}'. "
            "Pass allow_remote=True or --allow-remote to permit remote execution."
        )

    if config is None:
        config = Config()

    sorted_paths = sorted(file_paths, key=lambda p: Path(p).name)
    all_statements: list[ParsedStatement] = []

    for path in sorted_paths:
        p = Path(path)
        if not p.is_file():
            continue
        stmts = parse_sql_file(p)
        all_statements.extend(stmts)

    first_file = str(sorted_paths[0]) if sorted_paths else "<string>"
    return run_dynamic_verification(
        statements=all_statements,
        dsn=dsn,
        file_path=first_file,
        config=config,
    )
