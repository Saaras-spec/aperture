"""Unit tests for Aperture Typer CLI commands."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aperture.cli import app

runner = CliRunner()


def test_cli_help() -> None:
    """Verify help command displays usage information."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Scan PostgreSQL migration files" in result.output
    assert "check" in result.output
    assert "list-rules" in result.output
    assert "init" in result.output
    assert "explain" in result.output


def test_cli_list_rules() -> None:
    """Verify list-rules displays registered rules and exits 0."""
    result = runner.invoke(app, ["list-rules"])
    assert result.exit_code == 0
    assert "MG001" in result.output
    assert "MG005" in result.output
    assert "MG010" in result.output


def test_cli_explain_valid_rule() -> None:
    """Verify explain outputs detailed information for an existing rule."""
    result = runner.invoke(app, ["explain", "MG005"])
    assert result.exit_code == 0
    assert "MG005" in result.output
    assert "Why is this risky in production?" in result.output
    assert "CREATE INDEX CONCURRENTLY" in result.output


def test_cli_explain_nonexistent_rule() -> None:
    """Verify explain returns exit code 2 for nonexistent rule."""
    result = runner.invoke(app, ["explain", "INVALID_RULE_999"])
    assert result.exit_code == 2
    assert "not found" in result.output


def test_cli_init_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify init writes starter configuration to disk and exits 0."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert "Created aperture.yml successfully" in result.output
    assert (tmp_path / "aperture.yml").exists()
    content = (tmp_path / "aperture.yml").read_text(encoding="utf-8")
    assert "fail_on: ERROR" in content


def test_cli_init_refuses_overwrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify init refuses to overwrite existing config file and exits 2."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "aperture.yml").write_text("existing config", encoding="utf-8")
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 2
    assert "Refusing to overwrite" in result.output


def test_cli_check_safe_file_exits_0(tmp_path: Path) -> None:
    """Verify check exits 0 on safe migration."""
    migration = tmp_path / "001_safe.sql"
    migration.write_text("CREATE TABLE users (id serial PRIMARY KEY);", encoding="utf-8")

    result = runner.invoke(app, ["check", str(migration)])
    assert result.exit_code == 0
    assert "No migration risks detected" in result.output


def test_cli_check_risky_file_exits_1(tmp_path: Path) -> None:
    """Verify check exits 1 when finding exceeds fail_on severity."""
    migration = tmp_path / "002_risky.sql"
    migration.write_text("DROP TABLE users;", encoding="utf-8")

    result = runner.invoke(app, ["check", str(migration)])
    assert result.exit_code == 1
    assert "MG001" in result.output


def test_cli_check_format_json(tmp_path: Path) -> None:
    """Verify check --format json outputs valid JSON report."""
    migration = tmp_path / "003_risky.sql"
    migration.write_text("DROP TABLE users;", encoding="utf-8")

    result = runner.invoke(app, ["check", str(migration), "--format", "json"])
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["summary"]["errors"] == 1
    assert data["findings"][0]["rule_id"] == "MG001"


def test_cli_check_format_markdown(tmp_path: Path) -> None:
    """Verify check --format markdown produces markdown with hidden marker."""
    migration = tmp_path / "004_risky.sql"
    migration.write_text("DROP TABLE users;", encoding="utf-8")

    result = runner.invoke(app, ["check", str(migration), "--format", "markdown"])
    assert result.exit_code == 1
    assert "<!-- aperture -->" in result.output
    assert "## 🛡️ Aperture Report" in result.output


def test_cli_check_format_sarif(tmp_path: Path) -> None:
    """Verify check --format sarif produces valid SARIF document."""
    migration = tmp_path / "005_risky.sql"
    migration.write_text("DROP TABLE users;", encoding="utf-8")

    result = runner.invoke(app, ["check", str(migration), "--format", "sarif"])
    assert result.exit_code == 1
    sarif = json.loads(result.output)
    assert sarif["version"] == "2.1.0"
    assert len(sarif["runs"][0]["results"]) == 1


def test_cli_check_output_to_file(tmp_path: Path) -> None:
    """Verify check --output writes report to file."""
    migration = tmp_path / "006_risky.sql"
    migration.write_text("DROP TABLE users;", encoding="utf-8")
    out_file = tmp_path / "report.json"

    result = runner.invoke(
        app,
        ["check", str(migration), "--format", "json", "--output", str(out_file)],
    )
    assert result.exit_code == 1
    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["summary"]["total"] == 1


def test_cli_check_bad_config_exits_2(tmp_path: Path) -> None:
    """Verify check exits 2 when configuration file is invalid."""
    bad_cfg = tmp_path / "bad.yml"
    bad_cfg.write_text("fail_on: INVALID_LEVEL\n", encoding="utf-8")

    result = runner.invoke(app, ["check", "--config", str(bad_cfg)])
    assert result.exit_code == 2
    assert "Configuration error" in result.output


def test_cli_check_bad_syntax_sql_exits_2(tmp_path: Path) -> None:
    """Verify check exits 2 when SQL file has syntax errors."""
    syntax_err = tmp_path / "bad.sql"
    syntax_err.write_text("SELECT FROM WHERE;", encoding="utf-8")

    result = runner.invoke(app, ["check", str(syntax_err)])
    assert result.exit_code == 2
    assert "SQL parse error" in result.output


def test_cli_check_bad_format_option_exits_2(tmp_path: Path) -> None:
    """Verify check exits 2 when report format is unsupported."""
    migration = tmp_path / "007_safe.sql"
    migration.write_text("CREATE TABLE t (id int);", encoding="utf-8")

    result = runner.invoke(app, ["check", str(migration), "--format", "unsupported_fmt"])
    assert result.exit_code == 2
    assert "Report format error" in result.output


def test_cli_check_no_files_found(tmp_path: Path) -> None:
    """Verify check exits 0 when no files match."""
    result = runner.invoke(app, ["check", str(tmp_path / "empty_dir")])
    # Non-existent path raises DiscoveryError -> exit 2
    assert result.exit_code == 2

    # Empty directory with no sql files
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    result = runner.invoke(app, ["check", str(empty_dir)])
    assert result.exit_code == 0
    assert "No migration files found to scan" in result.output


def test_cli_check_with_explain_flag_fallback_without_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify check --explain runs cleanly and falls back when no API keys are configured."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    bad_migration = tmp_path / "008_unsafe.sql"
    bad_migration.write_text("ALTER TABLE users DROP COLUMN email;", encoding="utf-8")

    result = runner.invoke(app, ["check", str(bad_migration), "--explain"])
    # Exit code remains 1 because of policy failure (unchanged by explainer)
    assert result.exit_code == 1
    assert "MG001" in result.output
    assert "Dropping column 'email'" in result.output
    assert "aperture: AI explanation unavailable" in result.output


def test_cli_check_baseline_file_error_exits_2(tmp_path: Path) -> None:
    """Check with invalid or missing baseline file exits 2."""
    migration = tmp_path / "009_mig.sql"
    migration.write_text("CREATE TABLE t (id int);", encoding="utf-8")

    res = runner.invoke(app, ["check", str(migration), "--baseline", "does_not_exist.json"])
    assert res.exit_code == 2
    assert "Baseline error" in res.output


def test_cli_check_output_write_error_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check exits 2 when writing report to output file fails with OSError."""
    migration = tmp_path / "010_mig.sql"
    migration.write_text("CREATE TABLE t (id int);", encoding="utf-8")

    def mock_write_text(*_: object, **__: object) -> int:
        raise OSError("Disk full")

    monkeypatch.setattr(Path, "write_text", mock_write_text)
    out_file = tmp_path / "out.txt"
    res = runner.invoke(app, ["check", str(migration), "--output", str(out_file)])
    assert res.exit_code == 2
    assert "Failed to write output file" in res.output


def test_cli_init_write_error_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Init exits 2 when writing aperture.yml fails with OSError."""
    monkeypatch.chdir(tmp_path)

    def mock_write_text(*_: object, **__: object) -> int:
        raise OSError("Permission denied")

    monkeypatch.setattr(Path, "write_text", mock_write_text)
    res = runner.invoke(app, ["init"])
    assert res.exit_code == 2
    assert "Failed to write configuration file" in res.output


def test_cli_baseline_bad_config_exits_2(tmp_path: Path) -> None:
    """Baseline exits 2 on configuration error."""
    bad_cfg = tmp_path / "bad.yml"
    bad_cfg.write_text("fail_on: INVALID\n", encoding="utf-8")
    res = runner.invoke(app, ["baseline", "--config", str(bad_cfg)])
    assert res.exit_code == 2
    assert "Configuration error" in res.output


def test_cli_baseline_discovery_error_exits_2() -> None:
    """Baseline exits 2 on file discovery error."""
    res = runner.invoke(app, ["baseline", "path/that/definitely/does/not/exist"])
    assert res.exit_code == 2
    assert "File discovery error" in res.output


def test_cli_baseline_no_files_found(tmp_path: Path) -> None:
    """Baseline exits 0 when no migration files are found."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    res = runner.invoke(app, ["baseline", str(empty_dir)])
    assert res.exit_code == 0
    assert "No migration files found to baseline" in res.output


def test_cli_baseline_sql_parse_error_exits_2(tmp_path: Path) -> None:
    """Baseline exits 2 when a migration file has syntax errors."""
    bad_sql = tmp_path / "bad.sql"
    bad_sql.write_text("SELECT FROM WHERE;", encoding="utf-8")
    res = runner.invoke(app, ["baseline", str(bad_sql)])
    assert res.exit_code == 2
    assert "SQL parse error" in res.output


def test_cli_baseline_save_os_error_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Baseline exits 2 when saving baseline JSON fails with OSError."""
    mig = tmp_path / "mig.sql"
    mig.write_text("CREATE TABLE t (id int);", encoding="utf-8")

    def mock_save(*_: object, **__: object) -> None:
        raise OSError("Read-only filesystem")

    import aperture.baseline

    monkeypatch.setattr(aperture.baseline, "save_baseline", mock_save)
    res = runner.invoke(app, ["baseline", str(mig)])
    assert res.exit_code == 2
    assert "Failed to write baseline file" in res.output


def test_cli_fix_bad_config_exits_2(tmp_path: Path) -> None:
    """Fix exits 2 on configuration error."""
    bad_cfg = tmp_path / "bad.yml"
    bad_cfg.write_text("fail_on: INVALID\n", encoding="utf-8")
    res = runner.invoke(app, ["fix", "--config", str(bad_cfg)])
    assert res.exit_code == 2
    assert "Configuration error" in res.output


def test_cli_fix_discovery_error_exits_2() -> None:
    """Fix exits 2 on file discovery error."""
    res = runner.invoke(app, ["fix", "path/that/definitely/does/not/exist"])
    assert res.exit_code == 2
    assert "File discovery error" in res.output


def test_cli_fix_execution_error_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix exits 2 when fix_files raises an unexpected exception."""
    mig = tmp_path / "mig.sql"
    mig.write_text("CREATE TABLE t (id int);", encoding="utf-8")

    def mock_fix_files(*_: object, **__: object) -> list[object]:
        raise RuntimeError("Unexpected engine crash")

    import aperture.fixer

    monkeypatch.setattr(aperture.fixer, "fix_files", mock_fix_files)
    res = runner.invoke(app, ["fix", str(mig)])
    assert res.exit_code == 2
    assert "Error during auto-fix" in res.output


def test_cli_fix_write_mode_zero_files_modified(tmp_path: Path) -> None:
    """Fix with --write on already-clean file reports no fixable issues found."""
    mig = tmp_path / "clean.sql"
    mig.write_text("CREATE TABLE t (id int);", encoding="utf-8")
    res = runner.invoke(app, ["fix", str(mig), "--write"])
    assert res.exit_code == 0
    assert "No fixable issues found." in res.output


def test_cli_main_callable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify main() function executes without error."""
    from aperture.cli import main

    monkeypatch.setattr("sys.argv", ["aperture", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
