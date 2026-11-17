"""MG005: CREATE INDEX without CONCURRENTLY (locks writes)."""

from __future__ import annotations

from typing import Any, ClassVar

from aperture.models import Finding, ParsedStatement, Severity, StatementContext
from aperture.rules.base import Rule
from aperture.rules.helpers import get_table_name, is_table_created_in_run


class CreateIndexWithoutConcurrentlyRule(Rule):
    """Detects CREATE INDEX statements missing the CONCURRENTLY keyword."""

    id: ClassVar[str] = "MG005"
    name: ClassVar[str] = "CREATE INDEX without CONCURRENTLY"
    default_severity: ClassVar[Severity] = Severity.ERROR
    description: ClassVar[str] = (
        "CREATE INDEX without CONCURRENTLY blocks all concurrent write operations on the table."
    )

    def check(self, statement: ParsedStatement, context: StatementContext) -> list[Finding]:
        # Postgres behavior & locking:
        # - Standard CREATE INDEX acquires a SHARE lock on the target table.
        # - While reads are allowed, all concurrent writes (INSERT, UPDATE, DELETE) are blocked
        #   for the entire duration of the index build.
        # - On large production tables, this write freeze causes connection pools to exhaust.
        # Safe alternative:
        #   Use CREATE INDEX CONCURRENTLY:
        #   - Acquires only a SHARE UPDATE EXCLUSIVE lock.
        #   - Builds the index in two passes while allowing concurrent reads and writes.
        stmt: Any = statement.stmt
        table_name = get_table_name(stmt)

        if is_table_created_in_run(table_name, context):
            return []

        findings: list[Finding] = []

        if type(stmt).__name__ == "IndexStmt":
            is_concurrent = bool(getattr(stmt, "concurrent", False))
            if not is_concurrent:
                idx_name = getattr(stmt, "idxname", "index")
                tbl = table_name or "table"
                findings.append(
                    Finding(
                        rule_id=self.id,
                        severity=self.default_severity,
                        message=(
                            f"Creating index '{idx_name}' on table '{tbl}' without CONCURRENTLY "
                            "blocks all write operations until index creation completes."
                        ),
                        suggestion=(
                            f"Add the CONCURRENTLY keyword:\n"
                            f"   CREATE INDEX CONCURRENTLY {idx_name} ON ...;\n"
                            "Note: CONCURRENTLY cannot run inside a transaction block."
                        ),
                        file=context.file_path,
                        line=statement.start_line,
                        statement_sql=statement.sql,
                    )
                )

        return findings
