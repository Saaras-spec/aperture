"""MG001: DROP COLUMN / DROP TABLE (data loss)."""

from __future__ import annotations

from typing import Any, ClassVar

from pglast import enums

from migrationguard.models import Finding, ParsedStatement, Severity, StatementContext
from migrationguard.rules.base import Rule
from migrationguard.rules.helpers import get_table_name, is_table_created_in_same_file


class DropColumnOrTableRule(Rule):
    """Detects DROP TABLE or DROP COLUMN operations that cause immediate data loss."""

    id: ClassVar[str] = "MG001"
    name: ClassVar[str] = "DROP COLUMN or DROP TABLE"
    default_severity: ClassVar[Severity] = Severity.ERROR
    description: ClassVar[str] = (
        "Detects DROP TABLE or DROP COLUMN which causes irreversible data loss."
    )

    def check(self, statement: ParsedStatement, context: StatementContext) -> list[Finding]:
        # Postgres behavior & locking:
        # - DROP TABLE acquires an ACCESS EXCLUSIVE lock, blocking all concurrent reads and
        #   writes, and immediately deletes all data rows and table schema.
        # - ALTER TABLE ... DROP COLUMN acquires an ACCESS EXCLUSIVE lock, blocking all
        #   reads and writes, and drops column access immediately.
        # Safe alternative:
        #   Follow a deprecation cycle: first stop reading and writing the column/table in
        #   application code, deploy, monitor, and only drop after confirming zero references.
        stmt: Any = statement.stmt
        table_name = get_table_name(stmt)

        if is_table_created_in_same_file(table_name, context):
            return []

        findings: list[Finding] = []

        # Check DROP TABLE
        if type(stmt).__name__ == "DropStmt":
            remove_type = getattr(stmt, "removeType", None)
            if remove_type == enums.ObjectType.OBJECT_TABLE:
                target_desc = f"table '{table_name}'" if table_name else "table"
                findings.append(
                    Finding(
                        rule_id=self.id,
                        severity=self.default_severity,
                        message=f"Dropping {target_desc} causes irreversible data loss.",
                        suggestion=(
                            "Deprecate table in application code first. If data must be removed, "
                            "archive data or rename to a backup table before dropping."
                        ),
                        file=context.file_path,
                        line=statement.start_line,
                        statement_sql=statement.sql,
                    )
                )
                return findings

        # Check ALTER TABLE ... DROP COLUMN
        if type(stmt).__name__ == "AlterTableStmt":
            cmds = getattr(stmt, "cmds", None) or ()
            for cmd in cmds:
                subtype = getattr(cmd, "subtype", None)
                if subtype == enums.AlterTableType.AT_DropColumn:
                    col_name = getattr(cmd, "name", "")
                    col_desc = f"column '{col_name}'" if col_name else "column"
                    findings.append(
                        Finding(
                            rule_id=self.id,
                            severity=self.default_severity,
                            message=(
                                f"Dropping {col_desc} from table '{table_name}' "
                                "causes irreversible data loss."
                            ),
                            suggestion=(
                                f"Remove all references to '{col_name}' in application code first. "
                                "Deploy and monitor, then drop the column in a subsequent release."
                            ),
                            file=context.file_path,
                            line=statement.start_line,
                            statement_sql=statement.sql,
                        )
                    )

        return findings
