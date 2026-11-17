"""MG011: Foreign key added on a column with no index on referencing side."""

from __future__ import annotations

from typing import Any, ClassVar

from pglast import enums

from aperture.models import Finding, ParsedStatement, Severity, StatementContext
from aperture.rules.base import Rule
from aperture.rules.helpers import get_table_name


class ForeignKeyMissingIndexRule(Rule):
    """Detects foreign key additions lacking an index on the referencing columns."""

    id: ClassVar[str] = "MG011"
    name: ClassVar[str] = "ADD FOREIGN KEY without index on referencing column"
    default_severity: ClassVar[Severity] = Severity.WARNING
    description: ClassVar[str] = (
        "Adding a foreign key without an index on referencing columns causes table locks "
        "or sequential scans during deletes or updates on the referenced table."
    )

    def check(self, statement: ParsedStatement, context: StatementContext) -> list[Finding]:
        # Postgres behavior & locking:
        # - PostgreSQL does not automatically create an index on referencing foreign key columns.
        # - Whenever a row in the referenced table is DELETED or its primary key is UPDATED,
        #   PostgreSQL must verify that no rows in the referencing table reference that key.
        # - Without an index on the referencing side, PostgreSQL performs a sequential scan on the
        #   referencing table, holding locks that block concurrent writes.
        # Safe alternative:
        #   1. Create an index covering the foreign key columns concurrently:
        #      CREATE INDEX CONCURRENTLY idx_table_col ON table (col);
        #   2. Add the foreign key constraint with NOT VALID:
        #      ALTER TABLE table ADD CONSTRAINT fk_name
        #      FOREIGN KEY (col) REFERENCES ref (id) NOT VALID;
        #   3. Validate the constraint:
        #      ALTER TABLE table VALIDATE CONSTRAINT fk_name;
        stmt: Any = statement.stmt
        table_name = get_table_name(stmt)
        if not table_name:
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
                        fk_attrs = [
                            str(k.sval).lower() for k in getattr(c_def, "fk_attrs", ()) or ()
                        ]
                        if not fk_attrs:
                            continue

                        # If tracked schema has an index covering these columns, it is safe
                        if context.schema and context.schema.has_index_on_columns(
                            table_name, fk_attrs
                        ):
                            continue

                        cols_str = ", ".join(fk_attrs)
                        con_name = (
                            str(c_def.conname)
                            if getattr(c_def, "conname", None)
                            else f"fk_{table_name}_{'_'.join(fk_attrs)}"
                        )
                        pktable = (
                            str(c_def.pktable.relname)
                            if hasattr(c_def, "pktable") and c_def.pktable
                            else "referenced_table"
                        )
                        idx_name = f"idx_{table_name}_{'_'.join(fk_attrs)}"

                        findings.append(
                            Finding(
                                rule_id=self.id,
                                severity=self.default_severity,
                                message=(
                                    f"Foreign key '{con_name}' on table '{table_name}' "
                                    f"({cols_str}) references '{pktable}' but has no matching "
                                    "index on referencing columns. Deletes or key updates on "
                                    f"'{pktable}' will require a sequential scan or exclusive "
                                    f"lock on '{table_name}'."
                                ),
                                suggestion=(
                                    f"1. Create an index covering ({cols_str}) concurrently:\n"
                                    f"   CREATE INDEX CONCURRENTLY {idx_name} "
                                    f"ON {table_name} ({cols_str});\n"
                                    f"2. Add the foreign key constraint with NOT VALID:\n"
                                    f"   ALTER TABLE {table_name} ADD CONSTRAINT {con_name} "
                                    f"FOREIGN KEY ({cols_str}) REFERENCES {pktable} NOT VALID;\n"
                                    f"3. Validate the constraint:\n"
                                    f"   ALTER TABLE {table_name} VALIDATE CONSTRAINT {con_name};"
                                ),
                                file=context.file_path,
                                line=statement.start_line,
                                statement_sql=statement.sql,
                            )
                        )

        return findings
