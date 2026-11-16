"""MG006: ADD FOREIGN KEY without NOT VALID."""

from __future__ import annotations

from typing import Any, ClassVar

from pglast import enums

from aperture.models import Finding, ParsedStatement, Severity, StatementContext
from aperture.rules.base import Rule
from aperture.rules.helpers import get_table_name, is_table_created_in_same_file


class AddForeignKeyWithoutNotValidRule(Rule):
    """Detects ADD FOREIGN KEY constraints added without the NOT VALID clause."""

    id: ClassVar[str] = "MG006"
    name: ClassVar[str] = "ADD FOREIGN KEY without NOT VALID"
    default_severity: ClassVar[Severity] = Severity.ERROR
    description: ClassVar[str] = (
        "Adding a FOREIGN KEY without NOT VALID scans both tables under exclusive locks."
    )

    def check(self, statement: ParsedStatement, context: StatementContext) -> list[Finding]:
        # Postgres behavior & locking:
        # - Adding a FOREIGN KEY without NOT VALID takes a SHARE ROW EXCLUSIVE lock on the
        #   referencing table and a ROW SHARE lock on the referenced table.
        # - Postgres executes a sequential scan across the referencing table to check foreign
        #   keys, blocking concurrent writes on the table for the duration of the scan.
        # Safe alternative:
        #   1. Add the foreign key with NOT VALID (instant lock, validates only future writes):
        #      ALTER TABLE orders ADD CONSTRAINT fk_user
        #      FOREIGN KEY (user_id) REFERENCES users(id) NOT VALID;
        #   2. Validate the constraint in a subsequent transaction:
        #      ALTER TABLE orders VALIDATE CONSTRAINT fk_user;
        #      (Takes only SHARE UPDATE EXCLUSIVE lock, permitting concurrent writes).
        stmt: Any = statement.stmt
        table_name = get_table_name(stmt)

        if is_table_created_in_same_file(table_name, context):
            return []

        findings: list[Finding] = []

        if type(stmt).__name__ == "AlterTableStmt":
            cmds = getattr(stmt, "cmds", None) or ()
            for cmd in cmds:
                subtype = getattr(cmd, "subtype", None)
                if subtype == enums.AlterTableType.AT_AddConstraint:
                    c_def = getattr(cmd, "def_", None)
                    if c_def is None:
                        continue

                    contype = getattr(c_def, "contype", None)
                    if contype == enums.ConstrType.CONSTR_FOREIGN:
                        # When NOT VALID is specified, skip_validation is True
                        skip_val = bool(getattr(c_def, "skip_validation", False))
                        if not skip_val:
                            c_name = getattr(c_def, "conname", "foreign_key")
                            tbl = table_name or "table"
                            findings.append(
                                Finding(
                                    rule_id=self.id,
                                    severity=self.default_severity,
                                    message=(
                                        f"Adding FOREIGN KEY '{c_name}' on table '{tbl}' without "
                                        "NOT VALID blocks concurrent writes during full table scan."
                                    ),
                                    suggestion=(
                                        f"1. Add constraint with NOT VALID:\n"
                                        f"   ALTER TABLE {tbl} ADD CONSTRAINT {c_name}\n"
                                        f"   FOREIGN KEY (...) REFERENCES ... NOT VALID;\n"
                                        f"2. Validate in separate statement:\n"
                                        f"   ALTER TABLE {tbl} VALIDATE CONSTRAINT {c_name};"
                                    ),
                                    file=context.file_path,
                                    line=statement.start_line,
                                    statement_sql=statement.sql,
                                )
                            )

        return findings
