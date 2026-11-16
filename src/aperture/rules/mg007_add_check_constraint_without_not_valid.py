"""MG007: ADD CHECK constraint without NOT VALID."""

from __future__ import annotations

from typing import Any, ClassVar

from pglast import enums

from aperture.models import Finding, ParsedStatement, Severity, StatementContext
from aperture.rules.base import Rule
from aperture.rules.helpers import get_table_name, is_table_created_in_same_file


class AddCheckConstraintWithoutNotValidRule(Rule):
    """Detects ADD CHECK constraint added without NOT VALID."""

    id: ClassVar[str] = "MG007"
    name: ClassVar[str] = "ADD CHECK constraint without NOT VALID"
    default_severity: ClassVar[Severity] = Severity.WARNING
    description: ClassVar[str] = (
        "Adding a CHECK constraint without NOT VALID scans table under ACCESS EXCLUSIVE lock."
    )

    def check(self, statement: ParsedStatement, context: StatementContext) -> list[Finding]:
        # Postgres behavior & locking:
        # - Adding a CHECK constraint without NOT VALID acquires an ACCESS EXCLUSIVE lock.
        # - Postgres scans the whole table sequentially to verify existing rows satisfy the
        #   check condition, blocking all concurrent reads and writes during the scan.
        # Safe alternative:
        #   1. Add the CHECK constraint with NOT VALID:
        #      ALTER TABLE t ADD CONSTRAINT chk_name CHECK (...) NOT VALID;
        #      (Acquires a brief lock and validates only new/updated rows).
        #   2. Validate the constraint separately:
        #      ALTER TABLE t VALIDATE CONSTRAINT chk_name;
        #      (Validates existing rows under SHARE UPDATE EXCLUSIVE lock, allowing reads & writes).
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
                    if contype == enums.ConstrType.CONSTR_CHECK:
                        skip_val = bool(getattr(c_def, "skip_validation", False))
                        if not skip_val:
                            c_name = getattr(c_def, "conname", "check_constraint")
                            tbl = table_name or "table"
                            findings.append(
                                Finding(
                                    rule_id=self.id,
                                    severity=self.default_severity,
                                    message=(
                                        f"Adding CHECK constraint '{c_name}' on table '{tbl}' "
                                        "without NOT VALID blocks all queries during table scan."
                                    ),
                                    suggestion=(
                                        f"1. Add constraint with NOT VALID:\n"
                                        f"   ALTER TABLE {tbl} ADD CONSTRAINT {c_name} "
                                        f"CHECK (...) NOT VALID;\n"
                                        f"2. Validate in separate statement:\n"
                                        f"   ALTER TABLE {tbl} VALIDATE CONSTRAINT {c_name};"
                                    ),
                                    file=context.file_path,
                                    line=statement.start_line,
                                    statement_sql=statement.sql,
                                )
                            )

        return findings
