"""End-to-end engine tests using migration fixtures."""

from pathlib import Path

from aperture.config import Config, RuleConfig
from aperture.engine import check_file, compute_exit_code
from aperture.models import Severity

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
BAD_DIR = FIXTURES_DIR / "bad"
GOOD_DIR = FIXTURES_DIR / "good"


def test_e2e_bad_fixture_drop_table() -> None:
    """E2E: Dropping a table triggers MG001 and fails exit code."""
    bad_file = BAD_DIR / "mg001_drop_table.sql"
    config = Config(fail_on=Severity.ERROR)
    findings = check_file(bad_file, config)

    assert len(findings) >= 1
    mg001_findings = [f for f in findings if f.rule_id == "MG001"]
    assert len(mg001_findings) == 1
    assert mg001_findings[0].severity == Severity.ERROR
    assert compute_exit_code(findings, fail_on=Severity.ERROR) == 1


def test_e2e_bad_fixture_with_inline_suppression(tmp_path: Path) -> None:
    """E2E: Suppressing a risky statement passes exit code with 0."""
    sql = """
    -- aperture:ignore MG001 Removing legacy users table after migration to auth0
    DROP TABLE users;
    """
    migration_file = tmp_path / "001_suppressed.sql"
    migration_file.write_text(sql, encoding="utf-8")

    config = Config(fail_on=Severity.ERROR)
    findings = check_file(migration_file, config)

    assert len(findings) == 1
    assert findings[0].rule_id == "MG001"
    assert findings[0].suppressed is True
    assert "Removing legacy users table" in (findings[0].suppression_reason or "")
    # Should exit 0 because all findings are suppressed!
    assert compute_exit_code(findings, fail_on=Severity.ERROR) == 0


def test_e2e_bad_fixture_with_severity_override() -> None:
    """E2E: Overriding rule severity from ERROR to WARNING allows passing when fail_on=ERROR."""
    bad_file = BAD_DIR / "mg001_drop_table.sql"
    config = Config(
        fail_on=Severity.ERROR,
        rules={"MG001": RuleConfig(severity=Severity.WARNING)},
    )
    findings = check_file(bad_file, config)
    mg001 = next(f for f in findings if f.rule_id == "MG001")
    assert mg001.severity == Severity.WARNING
    # Exit code is 0 because finding is WARNING and fail_on is ERROR
    assert compute_exit_code(findings, fail_on=Severity.ERROR) == 0
    # But if fail_on is WARNING, exit code is 1
    assert compute_exit_code(findings, fail_on=Severity.WARNING) == 1


def test_e2e_good_fixtures_pass_cleanly() -> None:
    """E2E: All good fixtures run with default config and exit 0."""
    config = Config(fail_on=Severity.ERROR)
    for good_file in sorted(GOOD_DIR.glob("*.sql")):
        findings = check_file(good_file, config)
        assert compute_exit_code(findings, config.fail_on) == 0, (
            f"Fixture {good_file.name} produced failing findings: {findings}"
        )
