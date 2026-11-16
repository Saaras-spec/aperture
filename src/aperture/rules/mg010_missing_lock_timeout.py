"""MG010: Risky ALTER TABLE with no preceding SET lock_timeout in the same file."""

from __future__ import annotations

from typing import Any, ClassVar

from aperture.models import Finding, ParsedStatement, Severity, StatementContext
from aperture.rules.base import Rule
from aperture.rules.helpers import (
    get_table_name,
    has_preceding_lock_timeout,
    is_table_created_in_same_file,
)


class MissingLockTimeoutRule(Rule):
    """Detects risky ALTER TABLE operations without a preceding SET lock_timeout statement."""

    id: ClassVar[str] = "MG010"
    name: ClassVar[str] = "Risky ALTER TABLE without lock_timeout"
    default_severity: ClassVar[Severity] = Severity.WARNING
    description: ClassVar[str] = (
        "ALTER TABLE without lock_timeout can queue incoming traffic and cause outages."
    )

    def check(self, statement: ParsedStatement, context: StatementContext) -> list[Finding]:
        # Postgres behavior & locking:
        # - PostgreSQL uses a FIFO queue for lock acquisition.
        # - When ALTER TABLE requests ACCESS EXCLUSIVE, it waits for active queries to complete.
        # - Crucially, all new incoming queries (even quick SELECTs) get queued BEHIND the waiting
        #   ALTER TABLE and are blocked, quickly exhausting application connection pools.
        # Safe alternative:
        #   Set lock_timeout before running schema migrations:
        #   SET lock_timeout = '2s';
        #   If lock cannot be acquired within 2 seconds, Postgres aborts the migration immediately
        #   instead of piling up queries and taking down production.
        stmt: Any = statement.stmt
        table_name = get_table_name(stmt)

        if is_table_created_in_same_file(table_name, context):
            return []

        # Only evaluate on ALTER TABLE statements
        if type(stmt).__name__ != "AlterTableStmt":
            return []

        # If lock_timeout was already set earlier in this file, we are safe
        if has_preceding_lock_timeout(context):
            return []

        tbl = table_name or "table"
        return [
            Finding(
                rule_id=self.id,
                severity=self.default_severity,
                message=(
                    f"ALTER TABLE on '{tbl}' runs without a preceding SET lock_timeout in file."
                ),
                suggestion=(
                    "Add lock_timeout at the beginning of the migration file:\n"
                    "   SET lock_timeout = '2s';\n"
                    "This prevents the ALTER command from blocking connection pools if a lock "
                    "cannot be acquired immediately."
                ),
                file=context.file_path,
                line=statement.start_line,
                statement_sql=statement.sql,
            )
        ]
