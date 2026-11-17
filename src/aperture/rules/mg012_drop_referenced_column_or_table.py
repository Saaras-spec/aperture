"""MG012: DROP COLUMN or DROP TABLE with active dependents."""

from __future__ import annotations

from typing import Any, ClassVar

from pglast import enums

from aperture.models import Finding, ParsedStatement, Severity, StatementContext
from aperture.rules.base import Rule
from aperture.rules.helpers import get_table_name


class DropReferencedColumnOrTableRule(Rule):
    """Detects DROP TABLE or DROP COLUMN on relations/columns still referenced by active objects."""

    id: ClassVar[str] = "MG012"
    name: ClassVar[str] = "DROP COLUMN or DROP TABLE with active dependents"
    default_severity: ClassVar[Severity] = Severity.ERROR
    description: ClassVar[str] = (
        "Dropping a column or table that is still referenced by an existing index or foreign key "
        "causes unexpected lock contention or breaks foreign key relationships."
    )

    def check(self, statement: ParsedStatement, context: StatementContext) -> list[Finding]:
        # Postgres behavior & locking:
        # - Dropping a table referenced by a foreign key fails unless CASCADE is passed, which
        #   silently drops the constraint on the referencing table and weakens data integrity.
        # - Dropping a column that is referenced by an index drops the index automatically under
        #   an ACCESS EXCLUSIVE lock without CONCURRENTLY, causing lock queues and downtime.
        # Safe alternative:
        #   1. Drop dependent indexes first with DROP INDEX CONCURRENTLY.
        #   2. Drop dependent foreign key constraints first with ALTER TABLE ... DROP CONSTRAINT.
        #   3. Then proceed with the column or table drop.
        stmt: Any = statement.stmt
        if context.schema is None:
            return []

        findings: list[Finding] = []

        # Case 1: DROP TABLE
        if (
            type(stmt).__name__ == "DropStmt"
            and getattr(stmt, "removeType", None) == enums.ObjectType.OBJECT_TABLE
        ):
            for obj in getattr(stmt, "objects", ()) or ():
                if obj and hasattr(obj[-1], "sval"):
                    tbl = str(obj[-1].sval).lower()
                    ref_fks = context.schema.get_referencing_foreign_keys(tbl)
                    for ref_table, fk in ref_fks:
                        findings.append(
                            Finding(
                                rule_id=self.id,
                                severity=self.default_severity,
                                message=(
                                    f"Cannot safely drop table '{tbl}': referenced by foreign key "
                                    f"constraint '{fk.name}' on table '{ref_table}'. Postgres will "
                                    "reject the drop or drop constraints unexpectedly with CASCADE."
                                ),
                                suggestion=(
                                    f"1. Drop the referencing foreign key constraint first:\n"
                                    f"   ALTER TABLE {ref_table} DROP CONSTRAINT {fk.name};\n"
                                    f"2. Then drop table '{tbl}':\n"
                                    f"   DROP TABLE {tbl};"
                                ),
                                file=context.file_path,
                                line=statement.start_line,
                                statement_sql=statement.sql,
                            )
                        )

        # Case 2: ALTER TABLE ... DROP COLUMN
        elif type(stmt).__name__ == "AlterTableStmt":
            tbl_name = get_table_name(stmt)
            if not tbl_name:
                return []
            tbl = tbl_name.lower()

            cmds = getattr(stmt, "cmds", None) or ()
            for cmd in cmds:
                subtype = getattr(cmd, "subtype", None)
                if subtype == enums.AlterTableType.AT_DropColumn:
                    col_name = str(getattr(cmd, "name", "")).lower()

                    # Check for active indexes on this column
                    # WARNING severity: index is dropped automatically with the column
                    active_indexes = context.schema.get_indexes_on_column(tbl, col_name)
                    for idx in active_indexes:
                        findings.append(
                            Finding(
                                rule_id=self.id,
                                severity=Severity.WARNING,
                                message=(
                                    f"Cannot safely drop column '{col_name}' on table '{tbl}': "
                                    f"active index '{idx.name}' still references it. Postgres will "
                                    "drop the index automatically under exclusive lock."
                                ),
                                suggestion=(
                                    f"1. Drop the index concurrently first:\n"
                                    f"   DROP INDEX CONCURRENTLY {idx.name};\n"
                                    f"2. Then drop the column:\n"
                                    f"   ALTER TABLE {tbl} DROP COLUMN {col_name};"
                                ),
                                file=context.file_path,
                                line=statement.start_line,
                                statement_sql=statement.sql,
                            )
                        )

                    # Check for foreign keys referencing this column
                    active_fks = context.schema.get_referencing_foreign_keys(tbl, col_name)
                    for ref_table, fk in active_fks:
                        findings.append(
                            Finding(
                                rule_id=self.id,
                                severity=self.default_severity,
                                message=(
                                    f"Cannot safely drop column '{col_name}' on table '{tbl}': "
                                    f"referenced by foreign key constraint '{fk.name}' on "
                                    f"table '{ref_table}'."
                                ),
                                suggestion=(
                                    f"1. Drop the foreign key constraint first:\n"
                                    f"   ALTER TABLE {ref_table} DROP CONSTRAINT {fk.name};\n"
                                    f"2. Then drop the column:\n"
                                    f"   ALTER TABLE {tbl} DROP COLUMN {col_name};"
                                ),
                                file=context.file_path,
                                line=statement.start_line,
                                statement_sql=statement.sql,
                            )
                        )

        return findings
