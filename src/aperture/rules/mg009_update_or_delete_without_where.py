"""MG009: UPDATE or DELETE without WHERE (accidental data loss or table wipeout)."""

from __future__ import annotations

from typing import Any, ClassVar

from aperture.models import Finding, ParsedStatement, Severity, StatementContext
from aperture.rules.base import Rule
from aperture.rules.helpers import get_table_name, is_table_created_in_same_file


class UpdateOrDeleteWithoutWhereRule(Rule):
    """Detects UPDATE or DELETE statements executed without a WHERE clause."""

    id: ClassVar[str] = "MG009"
    name: ClassVar[str] = "UPDATE or DELETE without WHERE"
    default_severity: ClassVar[Severity] = Severity.ERROR
    description: ClassVar[str] = (
        "UPDATE or DELETE without WHERE modifies all rows, causing massive locks or data loss."
    )

    def check(self, statement: ParsedStatement, context: StatementContext) -> list[Finding]:
        # Postgres behavior & locking:
        # - UPDATE / DELETE without a WHERE clause scans and modifies every tuple in the relation.
        # - Acquires ROW EXCLUSIVE locks on all rows, creates massive WAL volume, causes table
        #   bloat, and in most migration scripts represents accidental data destruction.
        # Safe alternative:
        #   Add an explicit WHERE condition, or batch updates/deletes in small chunks
        #   (e.g., using cursor, primary key ranges, or batching scripts).
        stmt: Any = statement.stmt
        table_name = get_table_name(stmt)

        if is_table_created_in_same_file(table_name, context):
            return []

        findings: list[Finding] = []
        stmt_type = type(stmt).__name__

        if stmt_type in ("UpdateStmt", "DeleteStmt"):
            where_clause = getattr(stmt, "whereClause", None)
            if where_clause is None:
                op_name = "UPDATE" if stmt_type == "UpdateStmt" else "DELETE"
                tbl = table_name or "table"
                findings.append(
                    Finding(
                        rule_id=self.id,
                        severity=self.default_severity,
                        message=(
                            f"{op_name} on table '{tbl}' without a WHERE clause affects "
                            "every row in the table."
                        ),
                        suggestion=(
                            f"Add an explicit WHERE clause to restrict the {op_name} operation, "
                            "or backfill/delete rows in small, batched transactions."
                        ),
                        file=context.file_path,
                        line=statement.start_line,
                        statement_sql=statement.sql,
                    )
                )

        return findings
