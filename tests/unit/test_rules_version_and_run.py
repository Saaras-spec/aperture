"""Unit tests for PostgreSQL version awareness and cross-migration run exemptions."""

from __future__ import annotations

from pathlib import Path

from aperture.config import Config
from aperture.engine import check_files, check_sql_string


def test_created_table_in_earlier_migration_exempts_mg003_to_mg008(tmp_path: Path) -> None:
    """Operations on table created in earlier migration of the same run are exempt."""
    file1 = tmp_path / "001_create_accounts.sql"
    file1.write_text("CREATE TABLE accounts (id serial primary key, balance int);")

    file2 = tmp_path / "002_modify_accounts.sql"
    file2.write_text("""
    -- Operations on accounts (created in 001) should produce 0 findings for MG003-MG008
    ALTER TABLE accounts ADD COLUMN status text NOT NULL;
    ALTER TABLE accounts ALTER COLUMN balance SET NOT NULL;
    CREATE INDEX idx_accounts_balance ON accounts (balance);
    ALTER TABLE accounts ADD CONSTRAINT fk_user FOREIGN KEY (id) REFERENCES other_tbl(id);
    ALTER TABLE accounts ADD CONSTRAINT chk_bal CHECK (balance >= 0);
    ALTER TABLE accounts ALTER COLUMN balance TYPE bigint;
    """)

    config = Config()
    findings = check_files([file1, file2], config=config)

    # All of these operations on accounts are exempt
    mg_ids = {f.rule_id for f in findings}
    assert "MG003" not in mg_ids
    assert "MG004" not in mg_ids
    assert "MG005" not in mg_ids
    assert "MG006" not in mg_ids
    assert "MG007" not in mg_ids
    assert "MG008" not in mg_ids


def test_mg003_version_awareness_constant_default_pg11_plus() -> None:
    """In PG 11+, ADD COLUMN NOT NULL with constant DEFAULT is safe and does not rewrite table."""
    sql = "ALTER TABLE users ADD COLUMN active boolean NOT NULL DEFAULT true;"
    config_pg14 = Config(target_postgres_version=14)
    findings_pg14 = check_sql_string(sql, config=config_pg14)
    assert len([f for f in findings_pg14 if f.rule_id == "MG003"]) == 0


def test_mg003_version_awareness_default_pg_pre_11() -> None:
    """In PG < 11, ADD COLUMN NOT NULL with DEFAULT causes table rewrite under lock."""
    sql = "ALTER TABLE users ADD COLUMN active boolean NOT NULL DEFAULT true;"
    config_pg10 = Config(target_postgres_version=10)
    findings_pg10 = check_sql_string(sql, config=config_pg10)
    mg003_findings = [f for f in findings_pg10 if f.rule_id == "MG003"]
    assert len(mg003_findings) == 1
    assert "PostgreSQL < 11" in mg003_findings[0].message


def test_mg003_volatile_default_triggers_even_on_pg11_plus() -> None:
    """Volatile DEFAULT expressions require table rewrite even on PostgreSQL 11+."""
    sql = "ALTER TABLE users ADD COLUMN updated_at timestamp NOT NULL DEFAULT clock_timestamp();"
    config_pg14 = Config(target_postgres_version=14)
    findings = check_sql_string(sql, config=config_pg14)
    mg003_findings = [f for f in findings if f.rule_id == "MG003"]
    assert len(mg003_findings) == 1
    assert "volatile DEFAULT expression" in mg003_findings[0].message


def test_mg004_version_awareness_valid_check_constraint_pg12_plus() -> None:
    """In PG 12+, SET NOT NULL is safe if a valid CHECK (col IS NOT NULL) constraint exists."""
    sql = """
    ALTER TABLE users ADD CONSTRAINT chk_email_not_null CHECK (email IS NOT NULL);
    ALTER TABLE users ALTER COLUMN email SET NOT NULL;
    """
    config_pg14 = Config(target_postgres_version=14)
    findings = check_sql_string(sql, config=config_pg14)
    mg004_findings = [f for f in findings if f.rule_id == "MG004"]
    # Bypassed in PG 12+ due to valid check constraint
    assert len(mg004_findings) == 0


def test_mg004_version_awareness_valid_check_constraint_pre_pg12() -> None:
    """In PG < 12, SET NOT NULL performs table scan even if a valid CHECK constraint exists."""
    sql = """
    ALTER TABLE users ADD CONSTRAINT chk_email_not_null CHECK (email IS NOT NULL);
    ALTER TABLE users ALTER COLUMN email SET NOT NULL;
    """
    config_pg11 = Config(target_postgres_version=11)
    findings = check_sql_string(sql, config=config_pg11)
    mg004_findings = [f for f in findings if f.rule_id == "MG004"]
    assert len(mg004_findings) == 1


def test_mg004_not_valid_check_constraint_still_triggers_on_pg14() -> None:
    """If CHECK constraint is NOT VALID and unvalidated, SET NOT NULL still triggers MG004."""
    sql = """
    ALTER TABLE users ADD CONSTRAINT chk_email_not_null CHECK (email IS NOT NULL) NOT VALID;
    ALTER TABLE users ALTER COLUMN email SET NOT NULL;
    """
    config_pg14 = Config(target_postgres_version=14)
    findings = check_sql_string(sql, config=config_pg14)
    mg004_findings = [f for f in findings if f.rule_id == "MG004"]
    assert len(mg004_findings) == 1
