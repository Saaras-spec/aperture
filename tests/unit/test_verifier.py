"""Unit tests for dynamic verification module and CLI verify command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from aperture.cli import app
from aperture.models import Finding, Severity
from aperture.parser import parse_sql_string
from aperture.verifier import (
    LOCK_STRENGTH,
    StatementVerification,
    VerificationReport,
    evaluate_statement_comparison,
    format_verification_report,
    get_database_name,
    is_non_transactional_statement,
    mask_dsn,
    run_dynamic_verification,
    verify_file,
)

runner = CliRunner()


def test_mask_dsn_safely_masks_passwords() -> None:
    """mask_dsn hides credentials across URI schemes, queries, and key-value formats."""
    uri_dsn = "postgresql://aperture_user:super_secret_pw@localhost:5432/aperture_test"
    masked_uri = mask_dsn(uri_dsn)
    assert "super_secret_pw" not in masked_uri
    assert "aperture_user:****@localhost:5432/aperture_test" in masked_uri

    # With query parameter password
    uri_query = "postgresql://user:pw@host:5432/db?password=pw_in_query&sslmode=require"
    masked_query = mask_dsn(uri_query)
    assert "pw" not in masked_query
    assert "pw_in_query" not in masked_query
    assert "password=%2A%2A%2A%2A" in masked_query or "password=****" in masked_query

    # Key-value DSN
    kv_dsn = "host=localhost user=admin password='my_password' dbname=prod_db"
    masked_kv = mask_dsn(kv_dsn)
    assert "my_password" not in masked_kv
    assert "password=****" in masked_kv


def test_get_database_name_extracts_correct_dbname() -> None:
    """get_database_name accurately extracts target database from URIs and key-value strings."""
    assert get_database_name("postgresql://u:p@localhost:5432/my_test_db") == "my_test_db"
    assert get_database_name("postgresql://u:p@localhost:5432/my_test_db?ssl=true") == "my_test_db"
    assert get_database_name("host=localhost dbname='custom_db' port=5432") == "custom_db"
    assert get_database_name("host=localhost port=5432") == "unknown_database"


def test_lock_strength_hierarchy() -> None:
    """Verify lock hierarchy conforms to PostgreSQL documentation table-level lock ordering."""
    assert LOCK_STRENGTH["AccessShareLock"] < LOCK_STRENGTH["RowShareLock"]
    assert LOCK_STRENGTH["RowShareLock"] < LOCK_STRENGTH["RowExclusiveLock"]
    assert LOCK_STRENGTH["RowExclusiveLock"] < LOCK_STRENGTH["ShareUpdateExclusiveLock"]
    assert LOCK_STRENGTH["ShareUpdateExclusiveLock"] < LOCK_STRENGTH["ShareLock"]
    assert LOCK_STRENGTH["ShareLock"] < LOCK_STRENGTH["ShareRowExclusiveLock"]
    assert LOCK_STRENGTH["ShareRowExclusiveLock"] < LOCK_STRENGTH["ExclusiveLock"]
    assert LOCK_STRENGTH["ExclusiveLock"] < LOCK_STRENGTH["AccessExclusiveLock"]


def test_is_non_transactional_statement() -> None:
    """Detect statements that cannot execute inside an open transaction block."""
    concurrent_idx = parse_sql_string("CREATE INDEX CONCURRENTLY idx_u ON users (email);")
    assert is_non_transactional_statement(concurrent_idx[0]) is True

    standard_idx = parse_sql_string("CREATE INDEX idx_u ON users (email);")
    assert is_non_transactional_statement(standard_idx[0]) is False

    drop_concurrent = parse_sql_string("DROP INDEX CONCURRENTLY idx_u;")
    assert is_non_transactional_statement(drop_concurrent[0]) is True

    alter_stmt = parse_sql_string("ALTER TABLE users ADD COLUMN age INT;")
    assert is_non_transactional_statement(alter_stmt[0]) is False


def test_evaluate_statement_comparison_mg005() -> None:
    """Verify MG005 comparison records confirmation for ShareLock and mismatch otherwise."""
    f = Finding(
        rule_id="MG005",
        severity=Severity.ERROR,
        message="Blocks writes",
        suggestion="",
        file="mig.sql",
        line=1,
        statement_sql="CREATE INDEX idx ON t(a);",
    )

    # Confirmed case: ShareLock held
    sv_confirmed = StatementVerification(
        statement_index=0,
        sql="CREATE INDEX idx ON t(a);",
        line=1,
        file="mig.sql",
        strongest_locks={"t": "ShareLock"},
    )
    evaluate_statement_comparison(sv_confirmed, [f])
    assert len(sv_confirmed.confirmations) == 1
    assert "CONFIRMED" in sv_confirmed.confirmations[0]
    assert len(sv_confirmed.mismatches) == 0

    # Mismatch case: weaker lock observed
    sv_mismatch = StatementVerification(
        statement_index=0,
        sql="CREATE INDEX idx ON t(a);",
        line=1,
        file="mig.sql",
        strongest_locks={"t": "RowShareLock"},
    )
    evaluate_statement_comparison(sv_mismatch, [f])
    assert len(sv_mismatch.mismatches) == 1
    assert "MISMATCH" in sv_mismatch.mismatches[0]


def test_evaluate_statement_comparison_mg008() -> None:
    """Verify MG008 comparison records confirmation for table rewrite and mismatch otherwise."""
    f = Finding(
        rule_id="MG008",
        severity=Severity.ERROR,
        message="Rewrites table",
        suggestion="",
        file="mig.sql",
        line=1,
        statement_sql="ALTER TABLE t ALTER COLUMN c TYPE bigint;",
    )

    # Confirmed: relfilenode changed
    sv_confirmed = StatementVerification(
        statement_index=0,
        sql="ALTER TABLE t ALTER COLUMN c TYPE bigint;",
        line=1,
        file="mig.sql",
        table_rewrites={"t": True},
    )
    evaluate_statement_comparison(sv_confirmed, [f])
    assert len(sv_confirmed.confirmations) == 1
    assert "CONFIRMED (relfilenode changed)" in sv_confirmed.confirmations[0]

    # Mismatch: relfilenode unchanged
    sv_mismatch = StatementVerification(
        statement_index=0,
        sql="ALTER TABLE t ALTER COLUMN c TYPE bigint;",
        line=1,
        file="mig.sql",
        table_rewrites={"t": False},
    )
    evaluate_statement_comparison(sv_mismatch, [f])
    assert len(sv_mismatch.mismatches) == 1
    assert "MISMATCH (relfilenode unchanged" in sv_mismatch.mismatches[0]


def test_format_verification_report_rendering() -> None:
    """Report formatter produces structured output including mismatch section."""
    sv = StatementVerification(
        statement_index=0,
        sql="ALTER TABLE users ALTER COLUMN age TYPE bigint;",
        line=5,
        file="001.sql",
        strongest_locks={"users": "AccessExclusiveLock"},
        table_rewrites={"users": True},
        confirmations=["MG008 on 'users' predicted a rewrite: CONFIRMED (relfilenode changed)"],
        mismatches=["Test mismatch warning"],
    )
    report = VerificationReport(
        database_name="aperture_test",
        server_version="16.2",
        statements=[sv],
        total_statements=1,
        total_confirmed=1,
        total_mismatches=1,
    )

    formatted = format_verification_report(report)
    assert "Dynamic Migration Verification Report" in formatted
    assert "Database:       aperture_test" in formatted
    assert "Server Version: 16.2" in formatted
    assert "CONFIRMED (relfilenode changed)" in formatted
    assert "Static vs. Dynamic Mismatches Detected" in formatted
    assert "Line 5: Test mismatch warning" in formatted


def test_cli_verify_safety_flag_required(tmp_path: Path) -> None:
    """CLI verify refuses to run unless --i-understand-this-runs-the-migration is passed."""
    mig = tmp_path / "001.sql"
    mig.write_text("CREATE TABLE t (id int);", encoding="utf-8")

    res = runner.invoke(app, ["verify", str(mig), "--dsn", "postgresql://localhost/db"])
    assert res.exit_code == 2
    assert "Safety error" in res.output
    assert "--i-understand-this-runs-the-migration" in res.output


def test_cli_verify_missing_dsn_exits_2(tmp_path: Path) -> None:
    """CLI verify exits 2 if --dsn is empty."""
    mig = tmp_path / "001.sql"
    mig.write_text("CREATE TABLE t (id int);", encoding="utf-8")

    res = runner.invoke(app, ["verify", str(mig), "--i-understand-this-runs-the-migration"])
    assert res.exit_code == 2
    assert "Missing required option: '--dsn" in res.output


def test_cli_verify_no_files_found(tmp_path: Path) -> None:
    """CLI verify exits 0 when no files are discovered."""
    empty = tmp_path / "empty"
    empty.mkdir()
    res = runner.invoke(
        app,
        [
            "verify",
            str(empty),
            "--dsn",
            "postgresql://localhost/db",
            "--i-understand-this-runs-the-migration",
        ],
    )
    assert res.exit_code == 0
    assert "No migration files found to verify" in res.output


def test_cli_verify_discovery_error_exits_2() -> None:
    """CLI verify exits 2 on nonexistent path."""
    res = runner.invoke(
        app,
        [
            "verify",
            "nonexistent/dir",
            "--dsn",
            "postgresql://localhost/db",
            "--i-understand-this-runs-the-migration",
        ],
    )
    assert res.exit_code == 2
    assert "File discovery error" in res.output


def test_cli_verify_bad_config_exits_2(tmp_path: Path) -> None:
    """CLI verify exits 2 if config file is malformed."""
    mig = tmp_path / "001.sql"
    mig.write_text("SELECT 1;", encoding="utf-8")
    cfg = tmp_path / "bad_config.yml"
    cfg.write_text("rules: [invalid", encoding="utf-8")

    res = runner.invoke(
        app,
        [
            "verify",
            str(mig),
            "--config",
            str(cfg),
            "--dsn",
            "postgresql://localhost/db",
            "--i-understand-this-runs-the-migration",
        ],
    )
    assert res.exit_code == 2
    assert "Configuration error" in res.output


def test_cli_verify_error_masks_dsn_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI verify masks passwords when reporting dynamic verification errors."""
    mig = tmp_path / "001.sql"
    mig.write_text("SELECT 1;", encoding="utf-8")

    def mock_verify_files(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("failed to connect to postgresql://user:supersecretpass@host:5432/db")

    monkeypatch.setattr("aperture.verifier.verify_files", mock_verify_files)

    res = runner.invoke(
        app,
        [
            "verify",
            str(mig),
            "--dsn",
            "postgresql://user:supersecretpass@host:5432/db",
            "--i-understand-this-runs-the-migration",
        ],
    )
    assert res.exit_code == 2
    assert "supersecretpass" not in res.output
    assert "postgresql://user:****@host:5432/db" in res.output


def test_cli_verify_mismatch_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI verify exits 1 when report has mismatches."""
    mig = tmp_path / "001.sql"
    mig.write_text("SELECT 1;", encoding="utf-8")

    fake_report = VerificationReport(
        database_name="testdb",
        server_version="16.0",
        statements=[],
        total_statements=1,
        total_confirmed=0,
        total_mismatches=1,
    )
    monkeypatch.setattr("aperture.verifier.verify_files", lambda *_args, **_kwargs: fake_report)

    res = runner.invoke(
        app,
        [
            "verify",
            str(mig),
            "--dsn",
            "postgresql://localhost/testdb",
            "--i-understand-this-runs-the-migration",
        ],
    )
    assert res.exit_code == 1
    assert "Connecting to database: 'testdb'" in res.output


def test_cli_verify_clean_exits_0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI verify exits 0 when report has zero mismatches."""
    mig = tmp_path / "001.sql"
    mig.write_text("SELECT 1;", encoding="utf-8")

    fake_report = VerificationReport(
        database_name="testdb",
        server_version="16.0",
        statements=[],
        total_statements=1,
        total_confirmed=1,
        total_mismatches=0,
    )
    monkeypatch.setattr("aperture.verifier.verify_files", lambda *_args, **_kwargs: fake_report)

    res = runner.invoke(
        app,
        [
            "verify",
            str(mig),
            "--dsn",
            "postgresql://localhost/testdb",
            "--i-understand-this-runs-the-migration",
        ],
    )
    assert res.exit_code == 0
    assert "Connecting to database: 'testdb'" in res.output


def test_verify_file_not_found_raises(tmp_path: Path) -> None:
    """verify_file raises FileNotFoundError for missing file."""
    with pytest.raises(FileNotFoundError):
        verify_file(tmp_path / "nonexistent.sql", dsn="postgresql://localhost/db")


def test_run_dynamic_verification_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock verification executes in transaction, handles non-transactional, and rolls back."""
    sql = (
        "CREATE TABLE users (id INT PRIMARY KEY);\n"
        "CREATE INDEX CONCURRENTLY idx_users_email ON users (email);\n"
    )
    stmts = parse_sql_string(sql)

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_conn.info.server_version = "160002"

    # Mock sequence of cursor queries:
    # 1. SELECT pg_backend_pid() -> (12345,)
    # 2. SELECT c.relname, pg_relation_filenode (before) -> []
    # 3. cur.execute(ps.sql)
    # 4. SELECT c.relname, pg_relation_filenode (after) -> [('users', 16384)]
    # 5. SELECT c.relname, l.mode (pg_locks) -> [('users', 'AccessExclusiveLock')]
    mock_cursor.fetchone.side_effect = [(12345,)]
    mock_cursor.fetchall.side_effect = [
        [],  # before filenodes
        [("users", 16384)],  # after filenodes
        [("users", "AccessExclusiveLock")],  # pg_locks
    ]

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda *_, **__: mock_conn)
    mock_conn.__enter__.return_value = mock_conn

    report = run_dynamic_verification(stmts, dsn="postgresql://aperture_user:pw@localhost/test_db")
    assert report.database_name == "test_db"
    assert report.total_statements == 2
    assert report.total_non_transactional == 1
    assert report.statements[1].status == "NOT_VERIFIABLE_IN_TRANSACTION"
    # Ensure rollback was executed at the end
    mock_conn.rollback.assert_called()
