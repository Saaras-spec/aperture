"""Unit tests for individual rules MG001 to MG010 covering edge cases."""

from __future__ import annotations

from aperture.models import Finding, Severity, StatementContext
from aperture.parser import parse_sql_string
from aperture.rules import get_rule


def _check(rule_id: str, sql: str) -> list[Finding]:
    """Helper to parse SQL and run a specific rule on all statements."""
    rule = get_rule(rule_id)
    assert rule is not None, f"Rule {rule_id} not found"
    statements = parse_sql_string(sql, file_path="test.sql")
    findings: list[Finding] = []
    for idx, stmt in enumerate(statements):
        context = StatementContext(
            file_path="test.sql",
            statement_index=idx,
            all_statements=statements,
        )
        findings.extend(rule.check(stmt, context))
    return findings


# ============================================================================
# MG001: DROP COLUMN or DROP TABLE
# ============================================================================


def test_mg001_drop_table_triggers() -> None:
    """MG001 triggers on DROP TABLE."""
    findings = _check("MG001", "DROP TABLE users;")
    assert len(findings) == 1
    assert findings[0].rule_id == "MG001"
    assert findings[0].severity == Severity.ERROR
    assert "users" in findings[0].message


def test_mg001_drop_column_triggers() -> None:
    """MG001 triggers on ALTER TABLE DROP COLUMN."""
    findings = _check("MG001", "ALTER TABLE users DROP COLUMN email;")
    assert len(findings) == 1
    assert findings[0].rule_id == "MG001"
    assert "email" in findings[0].message


def test_mg001_newly_created_table_is_safe() -> None:
    """MG001 does not trigger on dropping a table/column created in the same file."""
    sql = """
    CREATE TABLE temp_users (id int, email text);
    ALTER TABLE temp_users DROP COLUMN email;
    DROP TABLE temp_users;
    """
    findings = _check("MG001", sql)
    assert findings == []


# ============================================================================
# MG002: Rename column or table
# ============================================================================


def test_mg002_rename_table_triggers() -> None:
    """MG002 triggers on ALTER TABLE RENAME TO."""
    findings = _check("MG002", "ALTER TABLE users RENAME TO customers;")
    assert len(findings) == 1
    assert findings[0].rule_id == "MG002"
    assert findings[0].severity == Severity.WARNING
    assert "customers" in findings[0].message


def test_mg002_rename_column_triggers() -> None:
    """MG002 triggers on ALTER TABLE RENAME COLUMN."""
    findings = _check("MG002", "ALTER TABLE users RENAME COLUMN email TO contact_email;")
    assert len(findings) == 1
    assert findings[0].rule_id == "MG002"
    assert "contact_email" in findings[0].message


def test_mg002_newly_created_table_is_safe() -> None:
    """MG002 does not trigger when renaming tables or columns created in the same file."""
    sql = """
    CREATE TABLE temp_t (id int, val text);
    ALTER TABLE temp_t RENAME COLUMN val TO description;
    """
    findings = _check("MG002", sql)
    assert findings == []


# ============================================================================
# MG003: ADD COLUMN NOT NULL without DEFAULT
# ============================================================================


def test_mg003_add_column_not_null_without_default_triggers() -> None:
    """MG003 triggers when adding NOT NULL column without DEFAULT."""
    findings = _check("MG003", "ALTER TABLE users ADD COLUMN age INT NOT NULL;")
    assert len(findings) == 1
    assert findings[0].rule_id == "MG003"
    assert findings[0].severity == Severity.ERROR


def test_mg003_add_column_not_null_with_default_is_safe() -> None:
    """MG003 allows adding NOT NULL column if DEFAULT is provided."""
    findings = _check("MG003", "ALTER TABLE users ADD COLUMN age INT NOT NULL DEFAULT 0;")
    assert findings == []


def test_mg003_add_column_nullable_is_safe() -> None:
    """MG003 allows adding nullable column."""
    findings = _check("MG003", "ALTER TABLE users ADD COLUMN age INT;")
    assert findings == []


def test_mg003_newly_created_table_is_safe() -> None:
    """MG003 does not trigger on newly created tables in the same file."""
    sql = """
    CREATE TABLE temp_t (id int);
    ALTER TABLE temp_t ADD COLUMN age INT NOT NULL;
    """
    findings = _check("MG003", sql)
    assert findings == []


# ============================================================================
# MG004: ALTER COLUMN SET NOT NULL on existing column
# ============================================================================


def test_mg004_set_not_null_triggers() -> None:
    """MG004 triggers on ALTER COLUMN SET NOT NULL."""
    findings = _check("MG004", "ALTER TABLE users ALTER COLUMN age SET NOT NULL;")
    assert len(findings) == 1
    assert findings[0].rule_id == "MG004"
    assert findings[0].severity == Severity.WARNING


def test_mg004_drop_not_null_is_safe() -> None:
    """MG004 does not trigger on ALTER COLUMN DROP NOT NULL."""
    findings = _check("MG004", "ALTER TABLE users ALTER COLUMN age DROP NOT NULL;")
    assert findings == []


def test_mg004_newly_created_table_is_safe() -> None:
    """MG004 does not trigger on newly created tables in same file."""
    sql = """
    CREATE TABLE temp_t (id int, age int);
    ALTER TABLE temp_t ALTER COLUMN age SET NOT NULL;
    """
    findings = _check("MG004", sql)
    assert findings == []


# ============================================================================
# MG005: CREATE INDEX without CONCURRENTLY
# ============================================================================


def test_mg005_create_index_without_concurrently_triggers() -> None:
    """MG005 triggers on CREATE INDEX without CONCURRENTLY."""
    findings = _check("MG005", "CREATE INDEX idx_users_email ON users(email);")
    assert len(findings) == 1
    assert findings[0].rule_id == "MG005"
    assert findings[0].severity == Severity.ERROR


def test_mg005_create_index_concurrently_is_safe() -> None:
    """MG005 allows CREATE INDEX CONCURRENTLY."""
    findings = _check("MG005", "CREATE INDEX CONCURRENTLY idx_users_email ON users(email);")
    assert findings == []


def test_mg005_newly_created_table_is_safe() -> None:
    """MG005 does not trigger for index on a table created in the same file."""
    sql = """
    CREATE TABLE temp_t (id int, email text);
    CREATE INDEX idx_temp_email ON temp_t(email);
    """
    findings = _check("MG005", sql)
    assert findings == []


# ============================================================================
# MG006: ADD FOREIGN KEY without NOT VALID
# ============================================================================


def test_mg006_add_fk_without_not_valid_triggers() -> None:
    """MG006 triggers when adding foreign key constraint without NOT VALID."""
    sql = "ALTER TABLE orders ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(id);"
    findings = _check("MG006", sql)
    assert len(findings) == 1
    assert findings[0].rule_id == "MG006"
    assert findings[0].severity == Severity.ERROR


def test_mg006_add_fk_with_not_valid_is_safe() -> None:
    """MG006 allows adding foreign key constraint with NOT VALID."""
    sql = (
        "ALTER TABLE orders ADD CONSTRAINT fk_user "
        "FOREIGN KEY (user_id) REFERENCES users(id) NOT VALID;"
    )
    findings = _check("MG006", sql)
    assert findings == []


def test_mg006_newly_created_table_is_safe() -> None:
    """MG006 does not trigger for FK added on a table created in the same file."""
    sql = """
    CREATE TABLE temp_orders (id int, user_id int);
    ALTER TABLE temp_orders ADD CONSTRAINT fk_u FOREIGN KEY (user_id) REFERENCES users(id);
    """
    findings = _check("MG006", sql)
    assert findings == []


# ============================================================================
# MG007: ADD CHECK constraint without NOT VALID
# ============================================================================


def test_mg007_add_check_without_not_valid_triggers() -> None:
    """MG007 triggers when adding CHECK constraint without NOT VALID."""
    sql = "ALTER TABLE users ADD CONSTRAINT chk_age CHECK (age >= 0);"
    findings = _check("MG007", sql)
    assert len(findings) == 1
    assert findings[0].rule_id == "MG007"
    assert findings[0].severity == Severity.WARNING


def test_mg007_add_check_with_not_valid_is_safe() -> None:
    """MG007 allows adding CHECK constraint with NOT VALID."""
    sql = "ALTER TABLE users ADD CONSTRAINT chk_age CHECK (age >= 0) NOT VALID;"
    findings = _check("MG007", sql)
    assert findings == []


def test_mg007_newly_created_table_is_safe() -> None:
    """MG007 does not trigger for CHECK constraint on table created in same file."""
    sql = """
    CREATE TABLE temp_users (id int, age int);
    ALTER TABLE temp_users ADD CONSTRAINT chk_age CHECK (age >= 0);
    """
    findings = _check("MG007", sql)
    assert findings == []


# ============================================================================
# MG008: ALTER COLUMN TYPE
# ============================================================================


def test_mg008_alter_column_type_triggers() -> None:
    """MG008 triggers when changing column data type."""
    sql = "ALTER TABLE users ALTER COLUMN age TYPE bigint;"
    findings = _check("MG008", sql)
    assert len(findings) == 1
    assert findings[0].rule_id == "MG008"
    assert findings[0].severity == Severity.ERROR


def test_mg008_newly_created_table_is_safe() -> None:
    """MG008 does not trigger on table created in same file."""
    sql = """
    CREATE TABLE temp_users (id int, age int);
    ALTER TABLE temp_users ALTER COLUMN age TYPE bigint;
    """
    findings = _check("MG008", sql)
    assert findings == []


# ============================================================================
# MG009: UPDATE or DELETE without WHERE
# ============================================================================


def test_mg009_update_without_where_triggers() -> None:
    """MG009 triggers on UPDATE without WHERE."""
    findings = _check("MG009", "UPDATE users SET status = 'active';")
    assert len(findings) == 1
    assert findings[0].rule_id == "MG009"
    assert findings[0].severity == Severity.ERROR


def test_mg009_delete_without_where_triggers() -> None:
    """MG009 triggers on DELETE without WHERE."""
    findings = _check("MG009", "DELETE FROM users;")
    assert len(findings) == 1
    assert findings[0].rule_id == "MG009"
    assert findings[0].severity == Severity.ERROR


def test_mg009_update_with_where_is_safe() -> None:
    """MG009 allows UPDATE with WHERE."""
    findings = _check("MG009", "UPDATE users SET status = 'active' WHERE id = 1;")
    assert findings == []


def test_mg009_delete_with_where_is_safe() -> None:
    """MG009 allows DELETE with WHERE."""
    findings = _check("MG009", "DELETE FROM users WHERE id = 1;")
    assert findings == []


def test_mg009_newly_created_table_is_safe() -> None:
    """MG009 does not trigger on a table created in the same file."""
    sql = """
    CREATE TABLE temp_users (id int, status text);
    UPDATE temp_users SET status = 'active';
    DELETE FROM temp_users;
    """
    findings = _check("MG009", sql)
    assert findings == []


# ============================================================================
# MG010: Risky ALTER TABLE without lock_timeout
# ============================================================================


def test_mg010_alter_table_without_lock_timeout_triggers() -> None:
    """MG010 triggers on ALTER TABLE when lock_timeout was not set."""
    findings = _check("MG010", "ALTER TABLE users ADD COLUMN bio text;")
    assert len(findings) == 1
    assert findings[0].rule_id == "MG010"
    assert findings[0].severity == Severity.WARNING


def test_mg010_with_preceding_lock_timeout_is_safe() -> None:
    """MG010 does not trigger if SET lock_timeout precedes the ALTER TABLE."""
    sql = """
    SET lock_timeout = '2s';
    ALTER TABLE users ADD COLUMN bio text;
    """
    findings = _check("MG010", sql)
    assert findings == []


def test_mg010_non_alter_table_statements_safe() -> None:
    """MG010 only evaluates on ALTER TABLE statements."""
    findings = _check("MG010", "SELECT 1; CREATE TABLE test (id int);")
    assert findings == []


def test_mg010_newly_created_table_is_safe() -> None:
    """MG010 does not trigger for ALTER on a table created in the same file."""
    sql = """
    CREATE TABLE temp_users (id int);
    ALTER TABLE temp_users ADD COLUMN bio text;
    """
    findings = _check("MG010", sql)
    assert findings == []


# ============================================================================
# Helpers Unit Tests
# ============================================================================


def test_helpers_edge_cases() -> None:
    """Verify helpers handle null/empty AST constructs gracefully."""
    from aperture.rules.helpers import (
        get_table_name,
        is_alter_table_drop_column,
        is_table_created_in_same_file,
    )

    assert get_table_name(None) is None

    empty_context = StatementContext(file_path="empty.sql")
    assert is_table_created_in_same_file(None, empty_context) is False
    assert is_table_created_in_same_file("users", empty_context) is False

    assert is_alter_table_drop_column(None) is False
