"""MG004: ALTER COLUMN SET NOT NULL on existing column (full scan + lock)."""

from __future__ import annotations

from typing import Any, ClassVar

from pglast import enums

from migrationguard.models import Finding, ParsedStatement, Severity, StatementContext
from migrationguard.rules.base import Rule
from migrationguard.rules.helpers import get_table_name, is_table_created_in_same_file


class SetNotNullOnExistingColumnRule(Rule):
    """Detects ALTER COLUMN SET NOT NULL which triggers a table scan under exclusive lock."""

    id: ClassVar[str] = "MG004"
    name: ClassVar[str] = "ALTER COLUMN SET NOT NULL on existing column"
    default_severity: ClassVar[Severity] = Severity.WARNING
    description: ClassVar[str] = (
        "Setting NOT NULL on existing column requires full table scan under ACCESS EXCLUSIVE lock."
    )

    def check(self, statement: ParsedStatement, context: StatementContext) -> list[Finding]:
        # Postgres behavior & locking:
        # - ALTER TABLE ... ALTER COLUMN ... SET NOT NULL acquires an ACCESS EXCLUSIVE lock.
        # - Postgres performs a sequential scan of the whole table to check for NULLs, blocking
        #   all concurrent reads and writes during the scan.
        # Safe alternative:
        #   1. Add a CHECK constraint with NOT VALID:
        #      ALTER TABLE t ADD CONSTRAINT chk_not_null CHECK (col IS NOT NULL) NOT VALID;
        #      (Acquires a brief lock without scanning existing rows).
        #   2. Validate the constraint in a separate transaction:
        #      ALTER TABLE t VALIDATE CONSTRAINT chk_not_null;
        #      (Takes only SHARE UPDATE EXCLUSIVE lock, allowing concurrent reads and writes).
        stmt: Any = statement.stmt
        table_name = get_table_name(stmt)

        if is_table_created_in_same_file(table_name, context):
            return []

        findings: list[Finding] = []

        if type(stmt).__name__ == "AlterTableStmt":
            cmds = getattr(stmt, "cmds", None) or ()
            for cmd in cmds:
                subtype = getattr(cmd, "subtype", None)
                if subtype == enums.AlterTableType.AT_SetNotNull:
                    col_name = getattr(cmd, "name", "")
                    col_desc = f"column '{col_name}'" if col_name else "column"
                    tbl = table_name or "table"
                    findings.append(
                        Finding(
                            rule_id=self.id,
                            severity=self.default_severity,
                            message=(
                                f"Setting NOT NULL on {col_desc} of table '{tbl}' requires a "
                                "full table scan while holding an ACCESS EXCLUSIVE lock."
                            ),
                            suggestion=(
                                f"1. Add a NOT VALID check constraint (instant lock):\n"
                                f"   ALTER TABLE {tbl} ADD CONSTRAINT chk_{col_name}_not_null "
                                f"CHECK ({col_name} IS NOT NULL) NOT VALID;\n"
                                f"2. Validate in background without blocking reads/writes:\n"
                                f"   ALTER TABLE {tbl} "
                                f"VALIDATE CONSTRAINT chk_{col_name}_not_null;"
                            ),
                            file=context.file_path,
                            line=statement.start_line,
                            statement_sql=statement.sql,
                        )
                    )

        return findings
