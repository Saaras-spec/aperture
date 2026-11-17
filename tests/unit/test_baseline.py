"""Unit tests for baseline mode, fingerprint stability, and filtering."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from aperture.baseline import (
    Baseline,
    BaselineFinding,
    compute_statement_fingerprint,
    create_baseline,
    filter_findings_with_baseline,
    load_baseline,
    normalize_file_path,
    save_baseline,
)
from aperture.cli import app
from aperture.config import Config
from aperture.engine import check_files, compute_exit_code
from aperture.models import Severity

runner = CliRunner()


def test_fingerprint_stability_across_whitespace_and_comments() -> None:
    """Fingerprint remains identical across spacing, indentation, and comments."""
    sql1 = "CREATE INDEX idx_users_email ON users (email);"
    sql2 = """
    -- Index for user search
    CREATE   INDEX   idx_users_email
       ON   users  (email) ;
    """
    sql3 = """
    /* Multi-line
       block comment */
    CREATE INDEX idx_users_email ON users(email);
    """

    fp1 = compute_statement_fingerprint(sql1)
    fp2 = compute_statement_fingerprint(sql2)
    fp3 = compute_statement_fingerprint(sql3)

    assert fp1 == fp2
    assert fp1 == fp3


def test_fingerprint_stability_when_lines_move(tmp_path: Path) -> None:
    """Fingerprint does not change when line numbers shift due to added lines above."""
    # File version 1: statement at line 1
    file_v1 = tmp_path / "001_mig.sql"
    file_v1.write_text("CREATE INDEX idx_v1 ON users (name);\n")
    findings_v1 = check_files([file_v1], Config())
    assert len(findings_v1) == 1
    fp1 = compute_statement_fingerprint(findings_v1[0].statement_sql)

    # File version 2: same statement moved 20 lines down
    file_v2 = tmp_path / "001_mig_moved.sql"
    padding = "\n" * 20
    file_v2.write_text(f"{padding}CREATE INDEX idx_v1 ON users (name);\n")
    findings_v2 = check_files([file_v2], Config())
    assert len(findings_v2) == 1
    fp2 = compute_statement_fingerprint(findings_v2[0].statement_sql)

    assert fp1 == fp2
    assert findings_v1[0].line != findings_v2[0].line


def test_fingerprint_changes_when_new_finding_appears() -> None:
    """A different statement produces a distinct fingerprint."""
    sql_a = "CREATE INDEX idx_users_email ON users (email);"
    sql_b = "DROP TABLE legacy_users;"

    fp_a = compute_statement_fingerprint(sql_a)
    fp_b = compute_statement_fingerprint(sql_b)

    assert fp_a != fp_b


def test_normalize_file_path_cross_platform() -> None:
    """File paths are normalized to relative POSIX format with forward slashes."""
    cwd = Path.cwd()
    sample = cwd / "migrations" / "001_init.sql"
    norm = normalize_file_path(sample, base_dir=cwd)
    assert norm == "migrations/001_init.sql"
    assert "\\" not in norm


def test_save_and_load_baseline(tmp_path: Path) -> None:
    """Baseline can be serialized to JSON and loaded back accurately."""
    baseline = Baseline(
        findings=[
            BaselineFinding(
                rule_id="MG005",
                file="migrations/001.sql",
                fingerprint="abc123def456",
            )
        ]
    )
    out_file = tmp_path / ".aperture-baseline.json"
    save_baseline(baseline, out_file)
    assert out_file.is_file()

    loaded = load_baseline(out_file)
    assert loaded.version == "1.0"
    assert len(loaded.findings) == 1
    assert loaded.findings[0].rule_id == "MG005"
    assert loaded.findings[0].file == "migrations/001.sql"
    assert loaded.findings[0].fingerprint == "abc123def456"


def test_filter_findings_with_baseline(tmp_path: Path) -> None:
    """Baseline filters existing findings and isolates newly added findings."""
    mig = tmp_path / "001_test.sql"
    mig.write_text("""
    CREATE INDEX idx_users_name ON users (name);
    """)

    findings = check_files([mig], Config())
    assert len(findings) == 1

    # Record baseline
    bl = create_baseline(findings, base_dir=tmp_path)
    assert len(bl.findings) == 1

    # First check with baseline: all filtered out
    new_findings, baselined = filter_findings_with_baseline(findings, bl, base_dir=tmp_path)
    assert len(new_findings) == 0
    assert len(baselined) == 1
    assert baselined[0].baselined is True
    assert compute_exit_code(baselined, Severity.ERROR) == 0

    # Now add a NEW finding to the migration
    mig.write_text("""
    CREATE INDEX idx_users_name ON users (name);
    DROP TABLE orders;
    """)

    updated_findings = check_files([mig], Config())
    assert len(updated_findings) == 2

    new_findings_2, baselined_2 = filter_findings_with_baseline(
        updated_findings, bl, base_dir=tmp_path
    )
    assert len(baselined_2) == 1
    assert baselined_2[0].rule_id == "MG005"
    assert len(new_findings_2) == 1
    assert new_findings_2[0].rule_id == "MG001"
    assert compute_exit_code(new_findings_2, Severity.ERROR) == 1


def test_cli_baseline_and_check_workflow(tmp_path: Path) -> None:
    """End-to-end CLI baseline creation and subsequent check with baseline flag."""
    mig = tmp_path / "001_bad.sql"
    mig.write_text("CREATE INDEX idx_bad ON accounts (num);\n")
    bl_file = tmp_path / ".baseline.json"

    # Step 1: Run 'aperture baseline' to capture findings
    result_bl = runner.invoke(
        app,
        ["baseline", str(mig), "--output", str(bl_file)],
    )
    assert result_bl.exit_code == 0
    assert "Recorded 1 finding(s)" in result_bl.stdout
    assert bl_file.is_file()

    # Step 2: Run 'aperture check --baseline' -> should pass with exit code 0
    result_check = runner.invoke(
        app,
        ["check", str(mig), "--baseline", str(bl_file)],
    )
    assert result_check.exit_code == 0
    assert "1 baselined" in result_check.stdout
    assert "Result: PASSED" in result_check.stdout

    # Step 3: Add new violation and verify check fails on the new finding
    mig.write_text("CREATE INDEX idx_bad ON accounts (num);\nDROP TABLE legacy_records;\n")
    result_check_new = runner.invoke(
        app,
        ["check", str(mig), "--baseline", str(bl_file)],
    )
    assert result_check_new.exit_code == 1
    assert "MG001" in result_check_new.stdout
    assert "1 baselined" in result_check_new.stdout
    assert "Result: FAILED" in result_check_new.stdout
