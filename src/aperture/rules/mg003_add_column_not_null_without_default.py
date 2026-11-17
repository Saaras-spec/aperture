"""MG003: ADD COLUMN ... NOT NULL without DEFAULT."""

from __future__ import annotations

from typing import Any, ClassVar

from pglast import enums

from aperture.models import Finding, ParsedStatement, Severity, StatementContext
from aperture.rules.base import Rule
from aperture.rules.helpers import get_table_name, is_table_created_in_run


def _is_volatile_expression(raw_expr: Any) -> bool:
    """Check if expression uses known volatile functions that trigger table rewrites."""
    if raw_expr is None:
        return False
    if type(raw_expr).__name__ == "FuncCall":
        func_names = getattr(raw_expr, "funcname", ()) or ()
        if func_names and hasattr(func_names[-1], "sval"):
            fn = str(func_names[-1].sval).lower()
            if fn in {"clock_timestamp", "random", "timeofday", "uuid_generate_v4"}:
                return True
    return False


class AddColumnNotNullWithoutDefaultRule(Rule):
    """Detects adding a NOT NULL column without a DEFAULT expression."""

    id: ClassVar[str] = "MG003"
    name: ClassVar[str] = "ADD COLUMN NOT NULL without DEFAULT"
    default_severity: ClassVar[Severity] = Severity.ERROR
    description: ClassVar[str] = (
        "Adding a NOT NULL column without DEFAULT requires table lock and fails on existing rows."
    )

    def check(self, statement: ParsedStatement, context: StatementContext) -> list[Finding]:
        # Postgres behavior & locking:
        # - ALTER TABLE ... ADD COLUMN ... NOT NULL without a DEFAULT requires an ACCESS EXCLUSIVE
        #   lock on the table, blocking all concurrent reads and writes.
        # - If the table already contains any rows, the statement immediately fails because
        #   existing rows would contain NULL values.
        # - PostgreSQL 11+ documentation (https://www.postgresql.org/docs/11/sql-altertable.html):
        #   Adding a column with non-volatile DEFAULT stores the value in catalog metadata
        #   without a full table rewrite.
        #   Release notes: https://www.postgresql.org/docs/11/release-11.html
        # - On PostgreSQL < 11, adding a column with DEFAULT (and especially NOT NULL DEFAULT)
        #   causes a full table rewrite holding an ACCESS EXCLUSIVE lock.
        # - On PostgreSQL 11+, volatile defaults (e.g. clock_timestamp()) still require a rewrite.
        stmt: Any = statement.stmt
        table_name = get_table_name(stmt)

        if is_table_created_in_run(table_name, context):
            return []

        findings: list[Finding] = []

        if type(stmt).__name__ == "AlterTableStmt":
            cmds = getattr(stmt, "cmds", None) or ()
            for cmd in cmds:
                subtype = getattr(cmd, "subtype", None)
                if subtype == enums.AlterTableType.AT_AddColumn:
                    col_def = getattr(cmd, "def_", None)
                    if col_def is None:
                        continue

                    col_name = getattr(col_def, "colname", "")
                    constraints = getattr(col_def, "constraints", None) or ()

                    has_not_null = any(
                        getattr(c, "contype", None) == enums.ConstrType.CONSTR_NOTNULL
                        for c in constraints
                    )
                    default_c = next(
                        (
                            c
                            for c in constraints
                            if getattr(c, "contype", None) == enums.ConstrType.CONSTR_DEFAULT
                        ),
                        None,
                    )
                    has_default = default_c is not None
                    target = (
                        f"column '{col_name}' to table '{table_name}'"
                        if table_name
                        else f"column '{col_name}'"
                    )
                    tbl = table_name or "t"

                    if has_not_null and not has_default:
                        findings.append(
                            Finding(
                                rule_id=self.id,
                                severity=self.default_severity,
                                message=(
                                    f"Adding NOT NULL {target} without DEFAULT will fail on "
                                    "non-empty tables and locks the relation."
                                ),
                                suggestion=(
                                    f"1. Add '{col_name}' as nullable:\n"
                                    f"   ALTER TABLE {tbl} ADD COLUMN {col_name} <type>;\n"
                                    f"2. Backfill existing rows in batches:\n"
                                    f"   UPDATE {tbl} SET {col_name} = <val>;\n"
                                    f"3. Add NOT NULL constraint with NOT VALID:\n"
                                    f"   ALTER TABLE {tbl} ADD CONSTRAINT chk_{col_name}_not_null "
                                    f"CHECK ({col_name} IS NOT NULL) NOT VALID;\n"
                                    f"4. Validate constraint:\n"
                                    f"   ALTER TABLE {tbl} "
                                    f"VALIDATE CONSTRAINT chk_{col_name}_not_null;"
                                ),
                                file=context.file_path,
                                line=statement.start_line,
                                statement_sql=statement.sql,
                            )
                        )
                    elif has_not_null and has_default and context.target_postgres_version < 11:
                        # PostgreSQL < 11: full table rewrite on ADD COLUMN NOT NULL DEFAULT
                        # Ref: PostgreSQL 10 docs: https://www.postgresql.org/docs/10/sql-altertable.html
                        findings.append(
                            Finding(
                                rule_id=self.id,
                                severity=Severity.WARNING,
                                message=(
                                    f"Adding NOT NULL {target} with DEFAULT on PostgreSQL < 11 "
                                    "rewrites the relation under ACCESS EXCLUSIVE lock."
                                ),
                                suggestion=(
                                    f"1. Add '{col_name}' as nullable:\n"
                                    f"   ALTER TABLE {tbl} ADD COLUMN {col_name} <type>;\n"
                                    f"2. Backfill existing rows in batches:\n"
                                    f"   UPDATE {tbl} SET {col_name} = <val>;\n"
                                    f"3. Set NOT NULL once backfill finishes."
                                ),
                                file=context.file_path,
                                line=statement.start_line,
                                statement_sql=statement.sql,
                            )
                        )
                    elif (
                        has_not_null
                        and default_c
                        and _is_volatile_expression(getattr(default_c, "raw_expr", None))
                    ):
                        # Ref: PostgreSQL 11 docs: https://www.postgresql.org/docs/11/sql-altertable.html
                        findings.append(
                            Finding(
                                rule_id=self.id,
                                severity=self.default_severity,
                                message=(
                                    f"Adding {target} with volatile DEFAULT expression "
                                    "requires a full table rewrite even on PostgreSQL 11+."
                                ),
                                suggestion=(
                                    f"1. Add '{col_name}' as nullable without volatile DEFAULT:\n"
                                    f"   ALTER TABLE {tbl} ADD COLUMN {col_name} <type>;\n"
                                    "2. Backfill rows in batches using application logic."
                                ),
                                file=context.file_path,
                                line=statement.start_line,
                                statement_sql=statement.sql,
                            )
                        )

        return findings
