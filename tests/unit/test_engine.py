"""Unit tests for the rule execution engine."""

from pathlib import Path

from migrationguard.config import Config, RuleConfig
from migrationguard.engine import (
    check_file,
    check_files,
    check_sql_string,
    check_statements,
    compute_exit_code,
)
from migrationguard.models import Finding, Severity
from migrationguard.parser import parse_sql_string


def test_engine_clean_sql() -> None:
    """Verify safe SQL returns zero findings."""
    sql = "CREATE TABLE users (id serial PRIMARY KEY);"
    config = Config()
    findings = check_sql_string(sql, config)
    assert findings == []
    assert compute_exit_code(findings, config.fail_on) == 0


def test_engine_detects_unsafe_sql() -> None:
    """Verify engine runs rules and detects violations."""
    sql = "DROP TABLE users;"
    config = Config()
    findings = check_sql_string(sql, config, file_path="001.sql")
    assert len(findings) == 1
    assert findings[0].rule_id == "MG001"
    assert findings[0].severity == Severity.ERROR
    assert findings[0].suppressed is False
    assert compute_exit_code(findings, fail_on=Severity.ERROR) == 1


def test_engine_rule_disabled() -> None:
    """Verify disabled rule produces no findings."""
    sql = "DROP TABLE users;"
    config = Config(rules={"MG001": RuleConfig(enabled=False)})
    findings = check_sql_string(sql, config)
    assert findings == []
    assert compute_exit_code(findings, config.fail_on) == 0


def test_engine_severity_override() -> None:
    """Verify per-rule severity override changes finding severity and exit code."""
    sql = "DROP TABLE users;"
    # Override MG001 to WARNING
    config = Config(
        fail_on=Severity.ERROR,
        rules={"MG001": RuleConfig(severity=Severity.WARNING)},
    )
    findings = check_sql_string(sql, config)
    assert len(findings) == 1
    assert findings[0].rule_id == "MG001"
    assert findings[0].severity == Severity.WARNING
    # Since fail_on is ERROR and finding is WARNING, exit code is 0!
    assert compute_exit_code(findings, fail_on=Severity.ERROR) == 0
    # But if fail_on is WARNING, exit code is 1
    assert compute_exit_code(findings, fail_on=Severity.WARNING) == 1


def test_engine_inline_suppression() -> None:
    """Verify inline suppression marks finding as suppressed and does not fail exit code."""
    sql = """
    -- migrationguard:ignore MG001 Dropping legacy table in maintenance window
    DROP TABLE users;
    """
    config = Config(fail_on=Severity.ERROR)
    findings = check_sql_string(sql, config)
    assert len(findings) == 1
    assert findings[0].rule_id == "MG001"
    assert findings[0].suppressed is True
    assert findings[0].suppression_reason == "Dropping legacy table in maintenance window"
    # Even though severity is ERROR, suppressed findings do not cause non-zero exit code
    assert compute_exit_code(findings, fail_on=Severity.ERROR) == 0


def test_engine_inline_suppression_missing_reason() -> None:
    """Verify missing reason in suppression produces MG000 and leaves rule unsuppressed."""
    sql = """
    -- migrationguard:ignore MG001
    DROP TABLE users;
    """
    config = Config(fail_on=Severity.WARNING)
    findings = check_sql_string(sql, config)
    # Expect 2 findings: MG000 (missing reason) and MG001 (unsuppressed)
    rule_ids = [f.rule_id for f in findings]
    assert "MG000" in rule_ids
    assert "MG001" in rule_ids

    mg001 = next(f for f in findings if f.rule_id == "MG001")
    assert mg001.suppressed is False

    # Exit code fails on WARNING because of MG000 (WARNING) and MG001 (ERROR)
    assert compute_exit_code(findings, fail_on=Severity.WARNING) == 1


def test_compute_exit_code_logic() -> None:
    """Verify compute_exit_code respects severity thresholds and suppression."""
    f_info = Finding("MG000", Severity.INFO, "msg", "sug", "f", 1, "sql")
    f_warn = Finding("MG002", Severity.WARNING, "msg", "sug", "f", 1, "sql")
    f_err = Finding("MG001", Severity.ERROR, "msg", "sug", "f", 1, "sql")
    f_err_supp = Finding("MG001", Severity.ERROR, "msg", "sug", "f", 1, "sql", suppressed=True)

    assert compute_exit_code([], Severity.ERROR) == 0
    assert compute_exit_code([f_info], Severity.WARNING) == 0
    assert compute_exit_code([f_warn], Severity.ERROR) == 0
    assert compute_exit_code([f_warn], Severity.WARNING) == 1
    assert compute_exit_code([f_err], Severity.ERROR) == 1
    assert compute_exit_code([f_err_supp], Severity.ERROR) == 0


def test_check_file_and_check_files(tmp_path: Path) -> None:
    """Verify check_file and check_files read from disk and aggregate findings."""
    f1 = tmp_path / "001_safe.sql"
    f1.write_text("CREATE TABLE t (id int);", encoding="utf-8")

    f2 = tmp_path / "002_risky.sql"
    f2.write_text("DROP TABLE t;", encoding="utf-8")

    config = Config()
    res1 = check_file(f1, config)
    assert res1 == []

    res2 = check_file(f2, config)
    assert len(res2) == 1
    assert res2[0].rule_id == "MG001"

    aggregated = check_files([f1, f2], config)
    assert len(aggregated) == 1
    assert aggregated[0].file == str(f2)


def test_check_statements_directly() -> None:
    """Verify check_statements directly receives and evaluates parsed statements."""
    stmts = parse_sql_string("DROP TABLE users;")
    config = Config()
    findings = check_statements(stmts, config, file_path="direct.sql")
    assert len(findings) == 1
    assert findings[0].file == "direct.sql"
