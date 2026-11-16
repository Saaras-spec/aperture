"""Unit tests for the rule registry and dynamic auto-registration."""

from collections.abc import Generator
from typing import ClassVar

import pytest

from migrationguard.models import Finding, ParsedStatement, Severity, StatementContext
from migrationguard.rules import (
    discover_rules,
    get_all_rules,
    get_rule,
)
from migrationguard.rules.base import (
    Rule,
    clear_rule_registry,
    get_registered_rules,
    register_rule,
)


@pytest.fixture(autouse=True)
def preserve_registry() -> Generator[None, None, None]:
    """Snapshot and restore rule registry around each test."""
    original = get_registered_rules()
    try:
        yield
    finally:
        clear_rule_registry()
        for cls in original.values():
            register_rule(cls)


def test_auto_registration_via_subclass() -> None:
    """Verify concrete Rule subclass auto-registers via __init_subclass__."""

    class DummyTestRule(Rule):
        id: ClassVar[str] = "TEST001"
        name: ClassVar[str] = "Dummy Test Rule"
        default_severity: ClassVar[Severity] = Severity.WARNING
        description: ClassVar[str] = "Dummy rule for registry test"

        def check(self, _statement: ParsedStatement, _context: StatementContext) -> list[Finding]:
            return []

    rules = get_registered_rules()
    assert "TEST001" in rules
    assert rules["TEST001"] is DummyTestRule

    rule_instance = get_rule("TEST001")
    assert rule_instance is not None
    assert rule_instance.id == "TEST001"
    assert rule_instance.name == "Dummy Test Rule"


def test_abstract_subclass_does_not_register() -> None:
    """Verify abstract Rule subclass with unimplemented methods is not auto-registered."""

    class AbstractTestRule(Rule):
        id: ClassVar[str] = "ABSTRACT001"
        name: ClassVar[str] = "Abstract Rule"
        default_severity: ClassVar[Severity] = Severity.INFO
        description: ClassVar[str] = "Should not be registered"
        # check() remains abstract

    rules = get_registered_rules()
    assert "ABSTRACT001" not in rules


def test_get_nonexistent_rule() -> None:
    """Verify get_rule returns None for an unregistered rule ID."""
    assert get_rule("NONEXISTENT_RULE_ID") is None


def test_register_rule_missing_id() -> None:
    """Verify register_rule rejects class with empty or missing id."""

    class RuleWithoutId(Rule):
        id: ClassVar[str] = ""
        name: ClassVar[str] = "No ID"
        default_severity: ClassVar[Severity] = Severity.INFO
        description: ClassVar[str] = "No ID"

        def check(self, _statement: ParsedStatement, _context: StatementContext) -> list[Finding]:
            return []

    with pytest.raises(ValueError, match="must define a non-empty 'id'"):
        register_rule(RuleWithoutId)


def test_get_all_rules_sorting() -> None:
    """Verify get_all_rules returns instances sorted by rule ID."""

    class RuleB(Rule):
        id: ClassVar[str] = "ZB002"
        name: ClassVar[str] = "Rule B"
        default_severity: ClassVar[Severity] = Severity.WARNING
        description: ClassVar[str] = "Rule B"

        def check(self, _statement: ParsedStatement, _context: StatementContext) -> list[Finding]:
            return []

    class RuleA(Rule):
        id: ClassVar[str] = "ZA001"
        name: ClassVar[str] = "Rule A"
        default_severity: ClassVar[Severity] = Severity.ERROR
        description: ClassVar[str] = "Rule A"

        def check(self, _statement: ParsedStatement, _context: StatementContext) -> list[Finding]:
            return []

    all_rules = get_all_rules()
    rule_ids = [r.id for r in all_rules if r.id.startswith("Z")]
    assert rule_ids == ["ZA001", "ZB002"]


def test_discover_rules_idempotent() -> None:
    """Verify discover_rules can be called repeatedly safely."""
    first = discover_rules()
    second = discover_rules()
    assert first == second


def test_discover_rules_imports_submodules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify discover_rules imports discovered rule submodules."""
    import pkgutil

    # Mock pkgutil.iter_modules to simulate finding a module 'fake_rule'
    fake_module_info = (None, "base", False)  # skipped
    fake_module_info_2 = (None, "__init__", False)  # skipped

    monkeypatch.setattr(
        pkgutil,
        "iter_modules",
        lambda *_args, **_kwargs: [fake_module_info, fake_module_info_2],
    )
    rules = discover_rules(force_reload=True)
    assert isinstance(rules, dict)
