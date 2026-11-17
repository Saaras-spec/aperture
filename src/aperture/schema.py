"""Schema state tracking across migration statements and files."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pglast import enums

from aperture.models import ParsedStatement


@dataclass
class ColumnState:
    """State of an individual table column."""

    name: str
    data_type: str = ""
    not_null: bool = False
    has_default: bool = False


@dataclass
class ConstraintState:
    """State of a table constraint."""

    name: str
    constraint_type: str  # "PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK"
    columns: tuple[str, ...] = ()
    foreign_table: str | None = None
    foreign_columns: tuple[str, ...] = ()
    check_not_null_column: str | None = None
    is_valid: bool = True


@dataclass
class IndexState:
    """State of an index."""

    name: str
    table_name: str
    columns: tuple[str, ...]
    is_unique: bool = False


@dataclass
class TableState:
    """State of an individual table."""

    name: str
    columns: dict[str, ColumnState] = field(default_factory=dict)
    constraints: dict[str, ConstraintState] = field(default_factory=dict)
    indexes: dict[str, IndexState] = field(default_factory=dict)
    created_in_run: bool = False


def _extract_check_not_null_column(raw_expr: Any) -> str | None:
    """Extract column name from a NullTest expr: CHECK (col IS NOT NULL)."""
    if raw_expr is None:
        return None
    if type(raw_expr).__name__ == "NullTest":
        nulltesttype = getattr(raw_expr, "nulltesttype", None)
        if nulltesttype == enums.NullTestType.IS_NOT_NULL:
            arg = getattr(raw_expr, "arg", None)
            if type(arg).__name__ == "ColumnRef":
                fields = getattr(arg, "fields", ()) or ()
                if fields and hasattr(fields[-1], "sval"):
                    return str(fields[-1].sval).lower()
    return None


class SchemaState:
    """Tracks schema evolution across SQL migration statements in temporal order."""

    def __init__(self) -> None:
        self.tables: dict[str, TableState] = {}
        self.created_tables_in_run: set[str] = set()
        self.indexes: dict[str, IndexState] = {}

    def _get_or_create_table(self, table_name: str) -> TableState:
        tbl = table_name.lower()
        if tbl not in self.tables:
            self.tables[tbl] = TableState(name=tbl)
        return self.tables[tbl]

    def is_table_created_in_run(self, table_name: str) -> bool:
        """Check if table was created in the current migration run."""
        return table_name.lower() in self.created_tables_in_run

    def has_index_on_columns(self, table_name: str, columns: Sequence[str]) -> bool:
        """Check if an index or unique/primary constraint covers the given columns as prefix."""
        if not columns:
            return False
        tbl = table_name.lower()
        target_cols = tuple(c.lower() for c in columns)
        k = len(target_cols)

        # Check indexes in schema for this table
        for idx in self.indexes.values():
            if idx.table_name == tbl and len(idx.columns) >= k and idx.columns[:k] == target_cols:
                return True

        # Also check table-level primary key / unique constraints
        if tbl in self.tables:
            for c in self.tables[tbl].constraints.values():
                if (
                    c.constraint_type in {"PRIMARY KEY", "UNIQUE"}
                    and len(c.columns) >= k
                    and c.columns[:k] == target_cols
                ):
                    return True

        return False

    def has_check_not_null(self, table_name: str, column_name: str) -> bool:
        """Check if a VALID CHECK (column IS NOT NULL) constraint exists."""
        tbl = table_name.lower()
        col = column_name.lower()
        if tbl not in self.tables:
            return False
        for c in self.tables[tbl].constraints.values():
            if c.constraint_type == "CHECK" and c.is_valid and c.check_not_null_column == col:
                return True
        return False

    def get_referencing_foreign_keys(
        self, table_name: str, column_name: str | None = None
    ) -> list[tuple[str, ConstraintState]]:
        """Find all foreign keys across all tables that reference the given table/column."""
        tbl = table_name.lower()
        col = column_name.lower() if column_name else None
        results: list[tuple[str, ConstraintState]] = []
        for ref_tbl_name, table_state in self.tables.items():
            for c in table_state.constraints.values():
                if (
                    c.constraint_type == "FOREIGN KEY"
                    and c.foreign_table == tbl
                    and (col is None or col in [fc.lower() for fc in c.foreign_columns])
                ):
                    results.append((ref_tbl_name, c))
        return results

    def get_indexes_on_column(self, table_name: str, column_name: str) -> list[IndexState]:
        """Find all indexes on table_name that contain column_name."""
        tbl = table_name.lower()
        col = column_name.lower()
        results: list[IndexState] = []
        for idx in self.indexes.values():
            if idx.table_name == tbl and col in idx.columns:
                results.append(idx)
        return results

    def get_constraints_on_column(self, table_name: str, column_name: str) -> list[ConstraintState]:
        """Find all constraints on table_name that contain column_name."""
        tbl = table_name.lower()
        col = column_name.lower()
        if tbl not in self.tables:
            return []
        results: list[ConstraintState] = []
        for c in self.tables[tbl].constraints.values():
            if col in c.columns or c.check_not_null_column == col:
                results.append(c)
        return results

    def apply_statement(self, statement: ParsedStatement) -> None:
        """Update schema state by reflecting the given parsed SQL statement."""
        stmt: Any = statement.stmt
        stmt_type = type(stmt).__name__

        if stmt_type == "CreateStmt":
            self._apply_create_table(stmt)
        elif stmt_type == "AlterTableStmt":
            self._apply_alter_table(stmt)
        elif stmt_type == "IndexStmt":
            self._apply_create_index(stmt)
        elif stmt_type == "DropStmt":
            self._apply_drop(stmt)
        elif stmt_type == "RenameStmt":
            self._apply_rename(stmt)

    def _apply_create_table(self, stmt: Any) -> None:
        if not hasattr(stmt, "relation") or not stmt.relation or not stmt.relation.relname:
            return
        tbl_name = str(stmt.relation.relname).lower()
        self.created_tables_in_run.add(tbl_name)
        table = self._get_or_create_table(tbl_name)
        table.created_in_run = True

        for elt in getattr(stmt, "tableElts", ()) or ():
            elt_type = type(elt).__name__
            if elt_type == "ColumnDef":
                self._add_column_def(table, elt)
            elif elt_type == "Constraint":
                self._add_constraint_node(table, elt)

    def _add_column_def(self, table: TableState, col_def: Any) -> None:
        col_name = str(col_def.colname).lower()
        col_state = ColumnState(name=col_name)
        table.columns[col_name] = col_state

        constraints = getattr(col_def, "constraints", ()) or ()
        for c in constraints:
            contype = getattr(c, "contype", None)
            if contype == enums.ConstrType.CONSTR_NOTNULL:
                col_state.not_null = True
            elif contype == enums.ConstrType.CONSTR_DEFAULT:
                col_state.has_default = True
            elif contype == enums.ConstrType.CONSTR_PRIMARY:
                col_state.not_null = True
                c_name = f"pk_{table.name}_{col_name}"
                table.constraints[c_name] = ConstraintState(
                    name=c_name,
                    constraint_type="PRIMARY KEY",
                    columns=(col_name,),
                )
                idx_state = IndexState(
                    name=c_name,
                    table_name=table.name,
                    columns=(col_name,),
                    is_unique=True,
                )
                table.indexes[c_name] = idx_state
                self.indexes[c_name] = idx_state
            elif contype == enums.ConstrType.CONSTR_UNIQUE:
                c_name = f"uq_{table.name}_{col_name}"
                table.constraints[c_name] = ConstraintState(
                    name=c_name,
                    constraint_type="UNIQUE",
                    columns=(col_name,),
                )
                idx_state = IndexState(
                    name=c_name,
                    table_name=table.name,
                    columns=(col_name,),
                    is_unique=True,
                )
                table.indexes[c_name] = idx_state
                self.indexes[c_name] = idx_state
            elif contype == enums.ConstrType.CONSTR_FOREIGN:
                fk_tbl = (
                    str(c.pktable.relname).lower() if hasattr(c, "pktable") and c.pktable else None
                )
                pk_attrs = tuple(str(k.sval).lower() for k in getattr(c, "pk_attrs", ()) or ())
                c_name = f"fk_{table.name}_{col_name}"
                table.constraints[c_name] = ConstraintState(
                    name=c_name,
                    constraint_type="FOREIGN KEY",
                    columns=(col_name,),
                    foreign_table=fk_tbl,
                    foreign_columns=pk_attrs,
                )
            elif contype == enums.ConstrType.CONSTR_CHECK:
                chk_col = _extract_check_not_null_column(getattr(c, "raw_expr", None))
                c_name = f"chk_{table.name}_{col_name}"
                table.constraints[c_name] = ConstraintState(
                    name=c_name,
                    constraint_type="CHECK",
                    columns=(col_name,),
                    check_not_null_column=chk_col,
                    is_valid=not bool(getattr(c, "skip_validation", False)),
                )

    def _add_constraint_node(self, table: TableState, c_node: Any) -> None:
        contype = getattr(c_node, "contype", None)
        con_name = (
            str(c_node.conname).lower()
            if getattr(c_node, "conname", None)
            else f"c_{len(table.constraints)}"
        )

        if contype == enums.ConstrType.CONSTR_PRIMARY:
            cols = tuple(str(k.sval).lower() for k in getattr(c_node, "keys", ()) or ())
            table.constraints[con_name] = ConstraintState(
                name=con_name,
                constraint_type="PRIMARY KEY",
                columns=cols,
            )
            idx_state = IndexState(
                name=con_name,
                table_name=table.name,
                columns=cols,
                is_unique=True,
            )
            table.indexes[con_name] = idx_state
            self.indexes[con_name] = idx_state
            for col in cols:
                if col in table.columns:
                    table.columns[col].not_null = True

        elif contype == enums.ConstrType.CONSTR_UNIQUE:
            cols = tuple(str(k.sval).lower() for k in getattr(c_node, "keys", ()) or ())
            table.constraints[con_name] = ConstraintState(
                name=con_name,
                constraint_type="UNIQUE",
                columns=cols,
            )
            idx_state = IndexState(
                name=con_name,
                table_name=table.name,
                columns=cols,
                is_unique=True,
            )
            table.indexes[con_name] = idx_state
            self.indexes[con_name] = idx_state

        elif contype == enums.ConstrType.CONSTR_FOREIGN:
            fk_cols = tuple(str(k.sval).lower() for k in getattr(c_node, "fk_attrs", ()) or ())
            fk_tbl = (
                str(c_node.pktable.relname).lower()
                if hasattr(c_node, "pktable") and c_node.pktable
                else None
            )
            pk_attrs = tuple(str(k.sval).lower() for k in getattr(c_node, "pk_attrs", ()) or ())
            table.constraints[con_name] = ConstraintState(
                name=con_name,
                constraint_type="FOREIGN KEY",
                columns=fk_cols,
                foreign_table=fk_tbl,
                foreign_columns=pk_attrs,
            )

        elif contype == enums.ConstrType.CONSTR_CHECK:
            chk_col = _extract_check_not_null_column(getattr(c_node, "raw_expr", None))
            table.constraints[con_name] = ConstraintState(
                name=con_name,
                constraint_type="CHECK",
                columns=(),
                check_not_null_column=chk_col,
                is_valid=not bool(getattr(c_node, "skip_validation", False)),
            )

    def _apply_alter_table(self, stmt: Any) -> None:
        if not hasattr(stmt, "relation") or not stmt.relation or not stmt.relation.relname:
            return
        tbl_name = str(stmt.relation.relname).lower()
        table = self._get_or_create_table(tbl_name)

        cmds = getattr(stmt, "cmds", ()) or ()
        for cmd in cmds:
            subtype = getattr(cmd, "subtype", None)

            if subtype == enums.AlterTableType.AT_AddColumn:
                col_def = getattr(cmd, "def_", None)
                if col_def:
                    self._add_column_def(table, col_def)

            elif subtype == enums.AlterTableType.AT_DropColumn:
                col_name = str(getattr(cmd, "name", "")).lower()
                if col_name in table.columns:
                    del table.columns[col_name]
                # Clean up indexes on this column
                for idx_k in list(table.indexes.keys()):
                    if col_name in table.indexes[idx_k].columns:
                        del table.indexes[idx_k]
                        if idx_k in self.indexes:
                            del self.indexes[idx_k]
                # Clean up constraints on this column
                for c_k in list(table.constraints.keys()):
                    c = table.constraints[c_k]
                    if col_name in c.columns or c.check_not_null_column == col_name:
                        del table.constraints[c_k]

            elif subtype == enums.AlterTableType.AT_AddConstraint:
                c_def = getattr(cmd, "def_", None)
                if c_def:
                    self._add_constraint_node(table, c_def)

            elif subtype == enums.AlterTableType.AT_DropConstraint:
                con_name = str(getattr(cmd, "name", "")).lower()
                if con_name in table.constraints:
                    del table.constraints[con_name]
                if con_name in table.indexes:
                    del table.indexes[con_name]
                if con_name in self.indexes:
                    del self.indexes[con_name]

            elif subtype == enums.AlterTableType.AT_ValidateConstraint:
                con_name = str(getattr(cmd, "name", "")).lower()
                if con_name in table.constraints:
                    table.constraints[con_name].is_valid = True

            elif subtype == enums.AlterTableType.AT_SetNotNull:
                col_name = str(getattr(cmd, "name", "")).lower()
                if col_name in table.columns:
                    table.columns[col_name].not_null = True

            elif subtype == enums.AlterTableType.AT_DropNotNull:
                col_name = str(getattr(cmd, "name", "")).lower()
                if col_name in table.columns:
                    table.columns[col_name].not_null = False

    def _apply_create_index(self, stmt: Any) -> None:
        if not hasattr(stmt, "relation") or not stmt.relation or not stmt.relation.relname:
            return
        tbl_name = str(stmt.relation.relname).lower()
        cols = []
        for elem in getattr(stmt, "indexParams", ()) or ():
            col_name = getattr(elem, "name", None)
            if col_name:
                cols.append(str(col_name).lower())

        idx_name = (
            str(stmt.idxname).lower()
            if getattr(stmt, "idxname", None)
            else f"idx_{tbl_name}_{'_'.join(cols)}"
        )
        idx_state = IndexState(
            name=idx_name,
            table_name=tbl_name,
            columns=tuple(cols),
            is_unique=bool(getattr(stmt, "unique", False)),
        )
        self.indexes[idx_name] = idx_state
        table = self._get_or_create_table(tbl_name)
        table.indexes[idx_name] = idx_state

    def _apply_drop(self, stmt: Any) -> None:
        remove_type = getattr(stmt, "removeType", None)
        objects = getattr(stmt, "objects", ()) or ()

        if remove_type == enums.ObjectType.OBJECT_TABLE:
            for obj in objects:
                if obj and hasattr(obj[-1], "sval"):
                    tbl = str(obj[-1].sval).lower()
                    if tbl in self.tables:
                        del self.tables[tbl]
                    for idx_k in list(self.indexes.keys()):
                        if self.indexes[idx_k].table_name == tbl:
                            del self.indexes[idx_k]

        elif remove_type == enums.ObjectType.OBJECT_INDEX:
            for obj in objects:
                if obj and hasattr(obj[-1], "sval"):
                    idx = str(obj[-1].sval).lower()
                    if idx in self.indexes:
                        tbl = self.indexes[idx].table_name
                        if tbl in self.tables and idx in self.tables[tbl].indexes:
                            del self.tables[tbl].indexes[idx]
                        del self.indexes[idx]

    def _apply_rename(self, stmt: Any) -> None:
        rename_type = getattr(stmt, "renameType", None)

        if rename_type == enums.ObjectType.OBJECT_TABLE:
            if hasattr(stmt, "relation") and stmt.relation and stmt.relation.relname:
                old_tbl = str(stmt.relation.relname).lower()
                new_tbl = str(stmt.newname).lower()
                if old_tbl in self.tables:
                    table = self.tables.pop(old_tbl)
                    table.name = new_tbl
                    self.tables[new_tbl] = table
                if old_tbl in self.created_tables_in_run:
                    self.created_tables_in_run.remove(old_tbl)
                    self.created_tables_in_run.add(new_tbl)
                for idx in self.indexes.values():
                    if idx.table_name == old_tbl:
                        idx.table_name = new_tbl
                for t in self.tables.values():
                    for c in t.constraints.values():
                        if c.foreign_table == old_tbl:
                            c.foreign_table = new_tbl

        elif rename_type == enums.ObjectType.OBJECT_COLUMN:
            if hasattr(stmt, "relation") and stmt.relation and stmt.relation.relname:
                tbl = str(stmt.relation.relname).lower()
                old_col = str(stmt.subname).lower()
                new_col = str(stmt.newname).lower()
                if tbl in self.tables:
                    table = self.tables[tbl]
                    if old_col in table.columns:
                        col = table.columns.pop(old_col)
                        col.name = new_col
                        table.columns[new_col] = col
                    for idx in table.indexes.values():
                        idx.columns = tuple(new_col if c == old_col else c for c in idx.columns)
                    for c in table.constraints.values():
                        c.columns = tuple(
                            new_col if c_col == old_col else c_col for c_col in c.columns
                        )
                        if c.check_not_null_column == old_col:
                            c.check_not_null_column = new_col

        elif (
            rename_type == enums.ObjectType.OBJECT_INDEX
            and hasattr(stmt, "relation")
            and stmt.relation
            and stmt.relation.relname
        ):
            old_idx = str(stmt.relation.relname).lower()
            new_idx = str(stmt.newname).lower()
            if old_idx in self.indexes:
                idx = self.indexes.pop(old_idx)
                idx.name = new_idx
                self.indexes[new_idx] = idx
                if idx.table_name in self.tables:
                    table = self.tables[idx.table_name]
                    if old_idx in table.indexes:
                        del table.indexes[old_idx]
                        table.indexes[new_idx] = idx
