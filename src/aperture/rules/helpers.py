"""Shared AST inspection helpers for Aperture rules."""

from __future__ import annotations

from typing import Any

from pglast import enums

from aperture.models import StatementContext


def get_table_name(stmt: Any) -> str | None:
    """Extract relation (table) name from various pglast AST statements."""
    if hasattr(stmt, "relation") and stmt.relation and hasattr(stmt.relation, "relname"):
        return str(stmt.relation.relname)

    # DropStmt contains objects tuple: ((<String sval='users'>,),)
    if type(stmt).__name__ == "DropStmt" and hasattr(stmt, "objects") and stmt.objects:
        first_obj = stmt.objects[0]
        if first_obj and hasattr(first_obj[-1], "sval"):
            return str(first_obj[-1].sval)

    return None


def is_table_created_in_same_file(table_name: str | None, context: StatementContext) -> bool:
    """Check if the given table was created in a preceding statement in the same migration file.

    Operations on newly created tables in the same migration are safe because the table
    is new, empty, and not yet receiving production queries.
    """
    if not table_name:
        return False

    target = table_name.lower()
    for prev_stmt in context.all_statements[: context.statement_index]:
        prev_node = prev_stmt.stmt
        if type(prev_node).__name__ == "CreateStmt" and hasattr(prev_node, "relation"):
            rel = getattr(prev_node, "relation", None)
            if (
                rel
                and hasattr(rel, "relname")
                and rel.relname
                and str(rel.relname).lower() == target
            ):
                return True

    return False


def is_table_created_in_run(table_name: str | None, context: StatementContext) -> bool:
    """Check if table was created in the current migration run or earlier in the same file.

    Operations on tables created in the same migration run are safe because the relation
    is newly created, empty, and not yet receiving production queries.
    """
    if not table_name:
        return False
    target = table_name.lower()
    if context.schema and context.schema.is_table_created_in_run(target):
        return True
    return is_table_created_in_same_file(table_name, context)


def has_preceding_lock_timeout(context: StatementContext) -> bool:
    """Check if any preceding statement in the file configured lock_timeout."""
    for prev_stmt in context.all_statements[: context.statement_index]:
        node = prev_stmt.stmt
        if type(node).__name__ == "VariableSetStmt":
            # VariableSetStmt has name attribute: 'lock_timeout'
            var_name = getattr(node, "name", "")
            if str(var_name).lower() == "lock_timeout":
                return True
    return False


def is_alter_table_drop_column(cmd: Any) -> bool:
    """Check if AlterTableCmd drops a column."""
    subtype = getattr(cmd, "subtype", None)
    return bool(subtype == enums.AlterTableType.AT_DropColumn)
