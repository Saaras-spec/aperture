"""Unit tests for Aperture auto-fixer engine and CLI fix command."""

from __future__ import annotations

import shutil
from pathlib import Path

from typer.testing import CliRunner

from aperture.cli import app
from aperture.config import Config
from aperture.engine import check_file, check_statements
from aperture.fixer import fix_file, fix_sql_string
from aperture.parser import parse_sql_file

runner = CliRunner()

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
BAD_DIR = FIXTURES_DIR / "bad"


def test_fix_mg005_creates_index_concurrently() -> None:
    """MG005 should rewrite CREATE INDEX to CREATE INDEX CONCURRENTLY."""
    sql = "CREATE INDEX idx_users_age ON users(age);\n"
    res = fix_sql_string(sql)

    assert res.modified is True
    assert "CREATE INDEX CONCURRENTLY idx_users_age ON users (age);" in res.rewritten
    assert "-CREATE INDEX idx_users_age ON users(age);" in res.diff
    assert "+CREATE INDEX CONCURRENTLY idx_users_age ON users (age);" in res.diff
    assert len(res.skipped_explanations) == 0


def test_fix_mg005_skipped_inside_transaction_block() -> None:
    """MG005 must NOT be fixed if wrapped inside BEGIN...COMMIT block."""
    sql = "BEGIN;\nCREATE INDEX idx_users_age ON users(age);\nCOMMIT;\n"
    res = fix_sql_string(sql)

    assert res.modified is False
    assert res.rewritten == sql
    assert len(res.skipped_explanations) == 1
    assert "transaction block" in res.skipped_explanations[0]


def test_fix_mg006_adds_not_valid_and_validate_and_lock_timeout() -> None:
    """MG006 rewrites ADD FOREIGN KEY to NOT VALID + VALIDATE CONSTRAINT + lock_timeout."""
    sql = (
        "ALTER TABLE orders ADD CONSTRAINT fk_orders_user "
        "FOREIGN KEY (user_id) REFERENCES users(id);\n"
    )
    res = fix_sql_string(sql)

    assert res.modified is True
    assert "SET lock_timeout = '2s';" in res.rewritten
    assert "FOREIGN KEY (user_id) REFERENCES users (id) NOT VALID;" in res.rewritten
    assert "ALTER TABLE orders VALIDATE CONSTRAINT fk_orders_user;" in res.rewritten


def test_fix_mg007_adds_not_valid_and_validate() -> None:
    """MG007 rewrites ADD CHECK constraint to NOT VALID + VALIDATE CONSTRAINT."""
    sql = "ALTER TABLE users ADD CONSTRAINT check_user_age CHECK (age >= 0);\n"
    res = fix_sql_string(sql)

    assert res.modified is True
    assert "SET lock_timeout = '2s';" in res.rewritten
    assert "CHECK (age >= 0) NOT VALID;" in res.rewritten
    assert "ALTER TABLE users VALIDATE CONSTRAINT check_user_age;" in res.rewritten


def test_fix_mg010_prepends_lock_timeout_only_once() -> None:
    """MG010 inserts lock_timeout at top of file only once even with multiple ALTER statements."""
    sql = "ALTER TABLE users ADD COLUMN age INT;\nALTER TABLE users ADD COLUMN name TEXT;\n"
    res = fix_sql_string(sql)

    assert res.modified is True
    assert res.rewritten.count("SET lock_timeout = '2s';") == 1
    assert res.rewritten.startswith("SET lock_timeout = '2s';\n\n")


def test_fix_without_write_never_modifies_file(tmp_path: Path) -> None:
    """Dry-run fix_file must never modify the target file on disk."""
    file_path = tmp_path / "migration.sql"
    orig_sql = "CREATE INDEX idx_users_age ON users(age);\n"
    file_path.write_text(orig_sql, encoding="utf-8")

    res = fix_file(file_path, write=False)

    assert res.modified is True
    assert res.written is False
    assert file_path.read_text(encoding="utf-8") == orig_sql


def test_fixable_bad_fixtures_pass_check_after_write(tmp_path: Path) -> None:
    """Applying fix with write=True to fixable fixtures must produce zero findings."""
    fixable_cases = [
        ("mg005_create_index_not_concurrent.sql", Config()),
        ("mg007_add_check_without_not_valid.sql", Config()),
        ("mg010_alter_table_no_lock_timeout.sql", Config()),
    ]

    for filename, cfg in fixable_cases:
        src = BAD_DIR / filename
        dst = tmp_path / filename
        shutil.copy(src, dst)

        res = fix_file(dst, write=True, config=cfg)
        assert res.modified is True
        assert res.written is True

        # Verify the rewritten file parses cleanly and produces zero findings
        stmts = parse_sql_file(dst)
        findings = check_statements(stmts, config=cfg, file_path=str(dst))
        assert len(findings) == 0, f"Expected 0 findings after fix for {filename}, got: {findings}"


def test_fix_mg006_with_index_passes_check_after_write(tmp_path: Path) -> None:
    """MG006 with preceding index passes check completely after fix with write=True."""
    sql = (
        "CREATE INDEX CONCURRENTLY idx_orders_user ON orders (user_id);\n"
        "ALTER TABLE orders ADD CONSTRAINT fk_user "
        "FOREIGN KEY (user_id) REFERENCES users(id);\n"
    )
    dst = tmp_path / "001_fk.sql"
    dst.write_text(sql, encoding="utf-8")

    res = fix_file(dst, write=True)
    assert res.modified is True
    assert res.written is True

    findings = check_file(dst, Config())
    assert len(findings) == 0, f"Expected 0 findings after fix, got: {findings}"


def test_cli_fix_dry_run_and_write_workflow(tmp_path: Path) -> None:
    """CLI fix prints diff in dry run and modifies file with --write."""
    migration = tmp_path / "001_index.sql"
    orig_sql = "CREATE INDEX idx_users_age ON users(age);\n"
    migration.write_text(orig_sql, encoding="utf-8")

    # 1. Dry run
    dry_run = runner.invoke(app, ["fix", str(migration)])
    assert dry_run.exit_code == 0
    assert "+CREATE INDEX CONCURRENTLY" in dry_run.output
    assert "would be modified. Run with --write to apply." in dry_run.output
    assert migration.read_text(encoding="utf-8") == orig_sql

    # 2. Write mode
    write_run = runner.invoke(app, ["fix", str(migration), "--write"])
    assert write_run.exit_code == 0
    assert "Fixed" in write_run.output
    assert "Successfully fixed 1 file(s)." in write_run.output
    assert "CONCURRENTLY" in migration.read_text(encoding="utf-8")

    # 3. Check passes now
    check_run = runner.invoke(app, ["check", str(migration)])
    assert check_run.exit_code == 0
    assert "All checks passed" in check_run.output


def test_cli_fix_transaction_block_warning(tmp_path: Path) -> None:
    """CLI fix warns when MG005 cannot be fixed due to transaction block."""
    migration = tmp_path / "002_txn.sql"
    migration.write_text(
        "BEGIN;\nCREATE INDEX idx_users_age ON users(age);\nCOMMIT;\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["fix", str(migration)])
    assert result.exit_code == 0
    assert "transaction block" in result.stderr or "transaction block" in result.output
    assert "No fixable issues found." in result.output


def test_fix_clean_file_produces_no_modifications(tmp_path: Path) -> None:
    """Running fix on already-safe SQL produces no modifications."""
    migration = tmp_path / "003_safe.sql"
    clean_sql = "SET lock_timeout = '2s';\nCREATE INDEX CONCURRENTLY idx_users ON users(id);\n"
    migration.write_text(clean_sql, encoding="utf-8")

    res = fix_file(migration, write=False)
    assert res.modified is False
    assert res.written is False
    assert res.diff == ""


def test_fix_anonymous_constraint_skipped_with_explanation() -> None:
    """Anonymous constraint cannot be validated and is skipped with explanation."""
    sql = "ALTER TABLE orders ADD FOREIGN KEY (user_id) REFERENCES users(id);\n"
    res = fix_sql_string(sql)
    assert any("anonymous" in s for s in res.skipped_explanations)


def test_fix_empty_or_whitespace_sql() -> None:
    """Empty or whitespace SQL returns unmodified FixResult."""
    res = fix_sql_string("   \n\t  ")
    assert res.modified is False
    assert res.diff == ""


def test_fix_file_not_found_raises(tmp_path: Path) -> None:
    """Fixing nonexistent file raises FileNotFoundError."""
    import pytest

    with pytest.raises(FileNotFoundError):
        fix_file(tmp_path / "nonexistent.sql")


def test_cli_fix_no_files_found(tmp_path: Path) -> None:
    """CLI fix with empty directory reports no migration files found."""
    result = runner.invoke(app, ["fix", str(tmp_path)])
    assert result.exit_code == 0
    assert "No migration files found to fix." in result.output
