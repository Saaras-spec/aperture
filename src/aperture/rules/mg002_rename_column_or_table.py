"""MG002: Rename column or table (breaks running app versions)."""

from __future__ import annotations

from typing import Any, ClassVar

from pglast import enums

from aperture.models import Finding, ParsedStatement, Severity, StatementContext
from aperture.rules.base import Rule
from aperture.rules.helpers import get_table_name, is_table_created_in_same_file


class RenameColumnOrTableRule(Rule):
    """Detects table or column rename operations that break running application versions."""

    id: ClassVar[str] = "MG002"
    name: ClassVar[str] = "Rename column or table"
    default_severity: ClassVar[Severity] = Severity.WARNING
    description: ClassVar[str] = (
        "Renaming a table or column causes running application queries using the old name to fail."
    )

    def check(self, statement: ParsedStatement, context: StatementContext) -> list[Finding]:
        # Postgres behavior & locking:
        # - ALTER TABLE ... RENAME [COLUMN|TO] acquires an ACCESS EXCLUSIVE lock on the table,
        #   blocking all concurrent reads and writes.
        # - Any currently running or rolling deployment instances still executing queries with
        #   the old name will instantly crash with 'relation/column does not exist'.
        # Safe alternative:
        #   Follow the expand-contract pattern:
        #   1. Add new column/table with the desired name.
        #   2. Dual-write to both old and new names (via application code or triggers).
        #   3. Backfill data from old to new.
        #   4. Switch application reads to the new name.
        #   5. Remove old column/table in a future deployment.
        stmt: Any = statement.stmt
        table_name = get_table_name(stmt)

        if is_table_created_in_same_file(table_name, context):
            return []

        findings: list[Finding] = []

        if type(stmt).__name__ == "RenameStmt":
            rename_type = getattr(stmt, "renameType", None)
            new_name = getattr(stmt, "newname", "")

            if rename_type == enums.ObjectType.OBJECT_TABLE:
                findings.append(
                    Finding(
                        rule_id=self.id,
                        severity=self.default_severity,
                        message=(
                            f"Renaming table '{table_name}' to '{new_name}' will break running "
                            "application versions querying the old table name."
                        ),
                        suggestion=(
                            f"Create a view or new table named '{new_name}', synchronize data, "
                            "update application code, and remove the old table in a later phase."
                        ),
                        file=context.file_path,
                        line=statement.start_line,
                        statement_sql=statement.sql,
                    )
                )

            elif rename_type == enums.ObjectType.OBJECT_COLUMN:
                old_col = getattr(stmt, "subname", "")
                findings.append(
                    Finding(
                        rule_id=self.id,
                        severity=self.default_severity,
                        message=(
                            f"Renaming column '{old_col}' to '{new_name}' on table '{table_name}' "
                            "will break running application versions querying the old column name."
                        ),
                        suggestion=(
                            f"Add the new column '{new_name}', dual-write to both columns, "
                            "backfill existing rows, update reads to the new column, "
                            "and drop the old column later."
                        ),
                        file=context.file_path,
                        line=statement.start_line,
                        statement_sql=statement.sql,
                    )
                )

        return findings
