"""Parametrized fixture tests for all Aperture rules.

Each bad fixture must trigger the expected rule.
Each good fixture must produce zero findings.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aperture.models import StatementContext
from aperture.parser import parse_sql_string
from aperture.rules import get_all_rules
from aperture.schema import SchemaState

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
BAD_DIR = FIXTURES_DIR / "bad"
GOOD_DIR = FIXTURES_DIR / "good"

# Map bad fixture filenames (without extension) to expected rule IDs.
# Filenames like "mg001_drop_table.sql" map to "MG001".
_BAD_FIXTURES = sorted(BAD_DIR.glob("*.sql"))
_GOOD_FIXTURES = sorted(GOOD_DIR.glob("*.sql"))


def _expected_rule_id(fixture_path: Path) -> str:
    """Extract expected rule ID from fixture filename.

    E.g. "mg001_drop_table.sql" -> "MG001"
    """
    stem = fixture_path.stem  # e.g. "mg001_drop_table"
    prefix = stem.split("_")[0]  # e.g. "mg001"
    return prefix.upper()  # e.g. "MG001"


def _run_all_rules(sql: str, file_path: str) -> list[tuple[str, str]]:
    """Parse SQL and run all rules, returning list of (rule_id, message) tuples."""
    statements = parse_sql_string(sql, file_path=file_path)
    rules = get_all_rules()
    results: list[tuple[str, str]] = []
    schema = SchemaState()

    for idx, stmt in enumerate(statements):
        context = StatementContext(
            file_path=file_path,
            statement_index=idx,
            all_statements=statements,
            schema=schema,
        )
        for rule in rules:
            findings = rule.check(stmt, context)
            for f in findings:
                results.append((f.rule_id, f.message))
        schema.apply_statement(stmt)

    return results


@pytest.mark.parametrize(
    "fixture_path",
    _BAD_FIXTURES,
    ids=[p.name for p in _BAD_FIXTURES],
)
def test_bad_fixture_triggers_expected_rule(fixture_path: Path) -> None:
    """Each bad fixture must trigger its expected rule ID at least once."""
    sql = fixture_path.read_text(encoding="utf-8")
    expected_id = _expected_rule_id(fixture_path)
    results = _run_all_rules(sql, file_path=str(fixture_path))
    triggered_ids = {rule_id for rule_id, _ in results}

    assert expected_id in triggered_ids, (
        f"Expected rule {expected_id} to fire for {fixture_path.name}, "
        f"but only got: {triggered_ids}"
    )


@pytest.mark.parametrize(
    "fixture_path",
    _GOOD_FIXTURES,
    ids=[p.name for p in _GOOD_FIXTURES],
)
def test_good_fixture_produces_no_findings(fixture_path: Path) -> None:
    """Each good fixture must produce zero findings from all rules."""
    sql = fixture_path.read_text(encoding="utf-8")
    results = _run_all_rules(sql, file_path=str(fixture_path))

    assert len(results) == 0, f"Expected zero findings for {fixture_path.name}, but got: {results}"
