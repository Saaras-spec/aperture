"""Unit tests for SchemaState tracking and query methods."""

from __future__ import annotations

from aperture.parser import parse_sql_string
from aperture.schema import SchemaState


def test_schema_create_table_and_columns() -> None:
    schema = SchemaState()
    sql = """
    CREATE TABLE users (
        id serial PRIMARY KEY,
        name text NOT NULL,
        email text UNIQUE,
        bio text DEFAULT 'hello'
    );
    """
    stmts = parse_sql_string(sql)
    schema.apply_statement(stmts[0])

    assert schema.is_table_created_in_run("users") is True
    assert "users" in schema.tables
    table = schema.tables["users"]
    assert table.created_in_run is True

    assert "id" in table.columns
    assert table.columns["id"].not_null is True

    assert "name" in table.columns
    assert table.columns["name"].not_null is True
    assert table.columns["name"].has_default is False

    assert "email" in table.columns
    assert "bio" in table.columns
    assert table.columns["bio"].has_default is True

    # Primary key and unique index should exist
    assert schema.has_index_on_columns("users", ["id"]) is True
    assert schema.has_index_on_columns("users", ["email"]) is True
    assert schema.has_index_on_columns("users", ["name"]) is False


def test_schema_table_level_constraints_and_foreign_keys() -> None:
    schema = SchemaState()
    sql = """
    CREATE TABLE orders (
        id int,
        user_id int,
        amount numeric,
        CONSTRAINT pk_orders PRIMARY KEY (id),
        CONSTRAINT fk_orders_user FOREIGN KEY (user_id) REFERENCES users (id)
    );
    """
    stmts = parse_sql_string(sql)
    schema.apply_statement(stmts[0])

    assert schema.is_table_created_in_run("orders") is True
    assert schema.has_index_on_columns("orders", ["id"]) is True
    # Foreign key user_id has no index by default
    assert schema.has_index_on_columns("orders", ["user_id"]) is False

    # Check referencing foreign keys
    ref_fks = schema.get_referencing_foreign_keys("users")
    assert len(ref_fks) == 1
    ref_table, fk_con = ref_fks[0]
    assert ref_table == "orders"
    assert fk_con.name == "fk_orders_user"
    assert fk_con.foreign_columns == ("id",)

    # Check column specific lookup
    assert len(schema.get_referencing_foreign_keys("users", "id")) == 1
    assert len(schema.get_referencing_foreign_keys("users", "other_col")) == 0


def test_schema_create_and_drop_index() -> None:
    schema = SchemaState()
    setup_sql = "CREATE TABLE users (id serial, email text, age int);"
    idx_sql = "CREATE INDEX idx_users_email_age ON users (email, age);"
    drop_idx_sql = "DROP INDEX idx_users_email_age;"

    schema.apply_statement(parse_sql_string(setup_sql)[0])
    schema.apply_statement(parse_sql_string(idx_sql)[0])

    assert schema.has_index_on_columns("users", ["email"]) is True
    assert schema.has_index_on_columns("users", ["email", "age"]) is True
    # "age" alone is not a prefix of (email, age)
    assert schema.has_index_on_columns("users", ["age"]) is False

    email_indexes = schema.get_indexes_on_column("users", "email")
    assert len(email_indexes) == 1
    assert email_indexes[0].name == "idx_users_email_age"

    schema.apply_statement(parse_sql_string(drop_idx_sql)[0])
    assert schema.has_index_on_columns("users", ["email"]) is False
    assert len(schema.get_indexes_on_column("users", "email")) == 0


def test_schema_alter_table_add_drop_column() -> None:
    schema = SchemaState()
    schema.apply_statement(parse_sql_string("CREATE TABLE users (id int);")[0])
    schema.apply_statement(parse_sql_string("ALTER TABLE users ADD COLUMN phone text NOT NULL;")[0])

    assert "phone" in schema.tables["users"].columns
    assert schema.tables["users"].columns["phone"].not_null is True

    # Drop column
    schema.apply_statement(parse_sql_string("ALTER TABLE users DROP COLUMN phone;")[0])
    assert "phone" not in schema.tables["users"].columns


def test_schema_check_constraint_and_validation() -> None:
    schema = SchemaState()
    schema.apply_statement(parse_sql_string("CREATE TABLE items (id int, sku text);")[0])

    # Add NOT VALID check constraint
    add_chk_sql = (
        "ALTER TABLE items ADD CONSTRAINT chk_sku_not_null CHECK (sku IS NOT NULL) NOT VALID;"
    )
    schema.apply_statement(parse_sql_string(add_chk_sql)[0])

    # Because it is NOT VALID, has_check_not_null should be False
    assert schema.has_check_not_null("items", "sku") is False

    # Validate constraint
    validate_sql = "ALTER TABLE items VALIDATE CONSTRAINT chk_sku_not_null;"
    schema.apply_statement(parse_sql_string(validate_sql)[0])

    # Now it is valid
    assert schema.has_check_not_null("items", "sku") is True


def test_schema_set_and_drop_not_null() -> None:
    schema = SchemaState()
    schema.apply_statement(parse_sql_string("CREATE TABLE items (id int, sku text);")[0])
    assert schema.tables["items"].columns["sku"].not_null is False

    schema.apply_statement(parse_sql_string("ALTER TABLE items ALTER COLUMN sku SET NOT NULL;")[0])
    assert schema.tables["items"].columns["sku"].not_null is True

    schema.apply_statement(parse_sql_string("ALTER TABLE items ALTER COLUMN sku DROP NOT NULL;")[0])
    assert schema.tables["items"].columns["sku"].not_null is False


def test_schema_drop_table() -> None:
    schema = SchemaState()
    schema.apply_statement(parse_sql_string("CREATE TABLE users (id int, email text);")[0])
    schema.apply_statement(parse_sql_string("CREATE INDEX idx_users_email ON users (email);")[0])
    assert "users" in schema.tables
    assert "idx_users_email" in schema.indexes

    schema.apply_statement(parse_sql_string("DROP TABLE users;")[0])
    assert "users" not in schema.tables
    assert "idx_users_email" not in schema.indexes


def test_schema_rename_table_and_column_and_index() -> None:
    schema = SchemaState()
    schema.apply_statement(parse_sql_string("CREATE TABLE users (id int, old_name text);")[0])
    schema.apply_statement(parse_sql_string("CREATE INDEX idx_name ON users (old_name);")[0])

    # Rename column
    schema.apply_statement(
        parse_sql_string("ALTER TABLE users RENAME COLUMN old_name TO new_name;")[0]
    )
    assert "old_name" not in schema.tables["users"].columns
    assert "new_name" in schema.tables["users"].columns
    assert schema.tables["users"].indexes["idx_name"].columns == ("new_name",)

    # Rename index
    schema.apply_statement(parse_sql_string("ALTER INDEX idx_name RENAME TO idx_new_name;")[0])
    assert "idx_name" not in schema.indexes
    assert "idx_new_name" in schema.indexes
    assert "idx_new_name" in schema.tables["users"].indexes

    # Rename table
    schema.apply_statement(parse_sql_string("ALTER TABLE users RENAME TO members;")[0])
    assert "users" not in schema.tables
    assert "members" in schema.tables
    assert schema.is_table_created_in_run("members") is True
    assert schema.indexes["idx_new_name"].table_name == "members"
