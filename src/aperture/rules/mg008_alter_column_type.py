"""MG008: ALTER COLUMN TYPE (table rewrite)."""

from __future__ import annotations

from typing import Any, ClassVar

from pglast import enums

from aperture.models import Finding, ParsedStatement, Severity, StatementContext
from aperture.rules.base import Rule
from aperture.rules.helpers import get_table_name, is_table_created_in_same_file


class AlterColumnTypeRule(Rule):
    """Detects ALTER COLUMN TYPE which can trigger a full table rewrite under exclusive lock."""

    id: ClassVar[str] = "MG008"
    name: ClassVar[str] = "ALTER COLUMN TYPE"
    default_severity: ClassVar[Severity] = Severity.ERROR
    description: ClassVar[str] = (
        "Altering a column type rewrites table and rebuilds indexes under ACCESS EXCLUSIVE lock."
    )

    def check(self, statement: ParsedStatement, context: StatementContext) -> list[Finding]:
        # Postgres behavior & locking:
        # - ALTER TABLE ... ALTER COLUMN ... TYPE acquires an ACCESS EXCLUSIVE lock.
        # - Unless the conversion is strictly binary-compatible, Postgres rewrites the entire
        #   table and rebuilds all indexes, blocking all reads and writes.
        # Safe alternative:
        #   1. Add new column with the target type:
        #      ALTER TABLE t ADD COLUMN new_col <new_type>;
        #   2. Dual-write to both columns in application logic or with a trigger.
        #   3. Backfill data in small batches.
        #   4. Switch application queries to read from new_col.
        #   5. Drop old_col in a later release.
        stmt: Any = statement.stmt
        table_name = get_table_name(stmt)

        if is_table_created_in_same_file(table_name, context):
            return []

        findings: list[Finding] = []

        if type(stmt).__name__ == "AlterTableStmt":
            cmds = getattr(stmt, "cmds", None) or ()
            for cmd in cmds:
                subtype = getattr(cmd, "subtype", None)
                if subtype == enums.AlterTableType.AT_AlterColumnType:
                    col_name = getattr(cmd, "name", "")
                    col_desc = f"column '{col_name}'" if col_name else "column"
                    tbl = table_name or "table"
                    findings.append(
                        Finding(
                            rule_id=self.id,
                            severity=self.default_severity,
                            message=(
                                f"Altering type of {col_desc} on table '{tbl}' can trigger a "
                                "full table rewrite while holding an ACCESS EXCLUSIVE lock."
                            ),
                            suggestion=(
                                f"1. Add a new column with desired type: "
                                f"ALTER TABLE {tbl} ADD COLUMN {col_name}_new <new_type>;\n"
                                f"2. Dual-write to both old and new columns in application code.\n"
                                f"3. Backfill data: UPDATE {tbl} SET {col_name}_new = ...;\n"
                                f"4. Switch application reads to {col_name}_new.\n"
                                f"5. Drop {col_name} in a later deployment."
                            ),
                            file=context.file_path,
                            line=statement.start_line,
                            statement_sql=statement.sql,
                        )
                    )

        return findings
