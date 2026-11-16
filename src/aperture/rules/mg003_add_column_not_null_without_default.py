"""MG003: ADD COLUMN ... NOT NULL without DEFAULT."""

from __future__ import annotations

from typing import Any, ClassVar

from pglast import enums

from aperture.models import Finding, ParsedStatement, Severity, StatementContext
from aperture.rules.base import Rule
from aperture.rules.helpers import get_table_name, is_table_created_in_same_file


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
        # - In PostgreSQL 11+, adding a column with DEFAULT is instant (metadata-only), but adding
        #   one WITHOUT default on a non-empty table is guaranteed to fail or block queries.
        # Safe alternative:
        #   1. Add the column as nullable first:
        #      ALTER TABLE t ADD COLUMN c <type>;
        #   2. Backfill existing rows in small batches:
        #      UPDATE t SET c = <default> WHERE c IS NULL;
        #   3. Add a NOT NULL check constraint with NOT VALID (instant lock):
        #      ALTER TABLE t ADD CONSTRAINT chk_not_null CHECK (c IS NOT NULL) NOT VALID;
        #   4. Validate the constraint without blocking table writes:
        #      ALTER TABLE t VALIDATE CONSTRAINT chk_not_null;
        stmt: Any = statement.stmt
        table_name = get_table_name(stmt)

        if is_table_created_in_same_file(table_name, context):
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
                    has_default = any(
                        getattr(c, "contype", None) == enums.ConstrType.CONSTR_DEFAULT
                        for c in constraints
                    )

                    if has_not_null and not has_default:
                        target = (
                            f"column '{col_name}' to table '{table_name}'"
                            if table_name
                            else f"column '{col_name}'"
                        )
                        tbl = table_name or "t"
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

        return findings
