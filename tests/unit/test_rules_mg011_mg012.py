"""Unit tests for schema-aware rules MG011 and MG012."""

from __future__ import annotations

from aperture.config import Config
from aperture.engine import check_sql_string
from aperture.models import Severity

# ============================================================================
# MG011: Foreign key missing index on referencing side
# ============================================================================


def test_mg011_bad_case_unindexed_foreign_key() -> None:
    """MG011 triggers WARNING when adding a foreign key with no supporting index."""
    sql = """
    ALTER TABLE orders ADD CONSTRAINT fk_orders_user
    FOREIGN KEY (user_id) REFERENCES users (id);
    """
    findings = check_sql_string(sql, config=Config())
    mg011_findings = [f for f in findings if f.rule_id == "MG011"]
    assert len(mg011_findings) == 1
    assert mg011_findings[0].severity == Severity.WARNING
    assert "user_id" in mg011_findings[0].message
    assert "orders" in mg011_findings[0].message


def test_mg011_good_case_preceding_index() -> None:
    """MG011 produces 0 findings when an index on referencing columns was created first."""
    sql = """
    CREATE INDEX CONCURRENTLY idx_orders_user_id ON orders (user_id);
    ALTER TABLE orders ADD CONSTRAINT fk_orders_user
    FOREIGN KEY (user_id) REFERENCES users (id) NOT VALID;
    """
    findings = check_sql_string(sql, config=Config())
    mg011_findings = [f for f in findings if f.rule_id == "MG011"]
    assert len(mg011_findings) == 0


def test_mg011_edge_case_composite_index_prefix() -> None:
    """MG011 produces 0 findings when a composite index covers referencing columns as prefix."""
    sql = """
    CREATE INDEX CONCURRENTLY idx_orders_tenant_user_created
    ON orders (tenant_id, user_id, created_at);

    ALTER TABLE orders ADD CONSTRAINT fk_orders_tenant_user
    FOREIGN KEY (tenant_id, user_id) REFERENCES accounts (tenant_id, id) NOT VALID;
    """
    findings = check_sql_string(sql, config=Config())
    mg011_findings = [f for f in findings if f.rule_id == "MG011"]
    assert len(mg011_findings) == 0


def test_mg011_index_leading_column_order_passes() -> None:
    """MG011 passes when foreign key column matches index leading column: (user_id, x)."""
    sql = """
    CREATE INDEX CONCURRENTLY idx_orders_user_x ON orders (user_id, x);
    ALTER TABLE orders ADD CONSTRAINT fk_orders_user
    FOREIGN KEY (user_id) REFERENCES users (id) NOT VALID;
    """
    findings = check_sql_string(sql, config=Config())
    mg011_findings = [f for f in findings if f.rule_id == "MG011"]
    assert len(mg011_findings) == 0


def test_mg011_index_non_leading_column_order_fails() -> None:
    """MG011 triggers WARNING when foreign key column is not leading in index: (x, user_id)."""
    sql = """
    CREATE INDEX CONCURRENTLY idx_orders_x_user ON orders (x, user_id);
    ALTER TABLE orders ADD CONSTRAINT fk_orders_user
    FOREIGN KEY (user_id) REFERENCES users (id) NOT VALID;
    """
    findings = check_sql_string(sql, config=Config())
    mg011_findings = [f for f in findings if f.rule_id == "MG011"]
    assert len(mg011_findings) == 1
    assert mg011_findings[0].severity == Severity.WARNING


# ============================================================================
# MG012: DROP COLUMN or DROP TABLE with active dependents
# ============================================================================


def test_mg012_bad_case_drop_table_with_referencing_fk() -> None:
    """MG012 triggers ERROR when dropping a table referenced by an active foreign key."""
    sql = """
    CREATE TABLE users (id serial primary key);
    CREATE TABLE orders (
        id serial primary key,
        user_id int,
        CONSTRAINT fk_orders_user FOREIGN KEY (user_id) REFERENCES users (id)
    );
    DROP TABLE users;
    """
    findings = check_sql_string(sql, config=Config())
    mg012_findings = [f for f in findings if f.rule_id == "MG012"]
    assert len(mg012_findings) == 1
    assert mg012_findings[0].severity == Severity.ERROR
    assert "users" in mg012_findings[0].message
    assert "fk_orders_user" in mg012_findings[0].message


def test_mg012_bad_case_drop_column_with_active_index_is_warning() -> None:
    """MG012 triggers WARNING when dropping a column with only an index dependency."""
    sql = """
    CREATE TABLE users (id serial, email text);
    CREATE INDEX idx_users_email ON users (email);
    ALTER TABLE users DROP COLUMN email;
    """
    findings = check_sql_string(sql, config=Config())
    mg012_findings = [f for f in findings if f.rule_id == "MG012"]
    assert len(mg012_findings) == 1
    assert mg012_findings[0].severity == Severity.WARNING
    assert "email" in mg012_findings[0].message
    assert "idx_users_email" in mg012_findings[0].message


def test_mg012_bad_case_drop_column_with_referencing_fk_is_error() -> None:
    """MG012 triggers ERROR when dropping a column referenced by a foreign key."""
    sql = """
    CREATE TABLE users (id serial primary key);
    CREATE TABLE orders (
        id serial primary key,
        user_id int,
        CONSTRAINT fk_orders_user FOREIGN KEY (user_id) REFERENCES users (id)
    );
    ALTER TABLE users DROP COLUMN id;
    """
    findings = check_sql_string(sql, config=Config())
    mg012_findings = [f for f in findings if f.rule_id == "MG012"]
    # Check that foreign key reference produces an ERROR finding
    fk_errors = [f for f in mg012_findings if f.severity == Severity.ERROR]
    assert len(fk_errors) == 1
    assert "id" in fk_errors[0].message
    assert "fk_orders_user" in fk_errors[0].message
    # Check that index on id produces a WARNING finding
    idx_warnings = [f for f in mg012_findings if f.severity == Severity.WARNING]
    assert len(idx_warnings) == 1


def test_mg012_good_case_drop_fk_before_table() -> None:
    """MG012 produces 0 findings when the foreign key constraint is dropped before the table."""
    sql = """
    CREATE TABLE users (id serial primary key);
    CREATE TABLE orders (
        id serial primary key,
        user_id int,
        CONSTRAINT fk_orders_user FOREIGN KEY (user_id) REFERENCES users (id)
    );
    ALTER TABLE orders DROP CONSTRAINT fk_orders_user;
    DROP TABLE users;
    """
    findings = check_sql_string(sql, config=Config())
    mg012_findings = [f for f in findings if f.rule_id == "MG012"]
    assert len(mg012_findings) == 0


def test_mg012_good_case_drop_index_before_column() -> None:
    """MG012 produces 0 findings when the index is dropped before the column."""
    sql = """
    CREATE TABLE users (id serial, email text);
    CREATE INDEX idx_users_email ON users (email);
    DROP INDEX idx_users_email;
    ALTER TABLE users DROP COLUMN email;
    """
    findings = check_sql_string(sql, config=Config())
    mg012_findings = [f for f in findings if f.rule_id == "MG012"]
    assert len(mg012_findings) == 0


def test_mg012_edge_case_drop_unreferenced_table_and_column() -> None:
    """MG012 produces 0 findings when dropping tables/columns with no dependent objects."""
    sql = """
    CREATE TABLE temp_logs (id serial, message text);
    ALTER TABLE temp_logs DROP COLUMN message;
    DROP TABLE temp_logs;
    """
    findings = check_sql_string(sql, config=Config())
    mg012_findings = [f for f in findings if f.rule_id == "MG012"]
    assert len(mg012_findings) == 0
