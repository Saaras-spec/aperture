"""Rule registry and dynamic rule discovery."""

from __future__ import annotations

import importlib
import pkgutil
from typing import TYPE_CHECKING

from migrationguard.rules.base import (
    Rule,
    clear_rule_registry,
    get_registered_rules,
    register_rule,
)

if TYPE_CHECKING:
    pass

_DISCOVERED = False


def discover_rules(force_reload: bool = False) -> dict[str, type[Rule]]:
    """Scan the rules directory and import all rule modules.

    Args:
        force_reload: If True, re-scans and re-imports modules even if already discovered.

    Returns:
        A dictionary mapping rule IDs to their Rule classes.
    """
    global _DISCOVERED
    if _DISCOVERED and not force_reload:
        return get_registered_rules()

    # Discover and import all modules in this package
    package_name = __name__
    for _, module_name, _ in pkgutil.iter_modules(__path__):
        if module_name in ("base", "__init__", "helpers"):
            continue
        full_module_name = f"{package_name}.{module_name}"
        importlib.import_module(full_module_name)

    _DISCOVERED = True
    return get_registered_rules()


def get_rule_classes() -> dict[str, type[Rule]]:
    """Get all registered rule classes, discovering them if necessary."""
    discover_rules()
    return get_registered_rules()


def get_rule(rule_id: str) -> Rule | None:
    """Retrieve an instantiated rule by its ID, or None if not found."""
    rule_classes = get_rule_classes()
    rule_cls = rule_classes.get(rule_id)
    if rule_cls is None:
        return None
    return rule_cls()


def get_all_rules() -> list[Rule]:
    """Retrieve instances of all registered rules, sorted by ID."""
    rule_classes = get_rule_classes()
    rules = [cls() for cls in rule_classes.values()]
    rules.sort(key=lambda r: r.id)
    return rules


__all__ = [
    "Rule",
    "clear_rule_registry",
    "discover_rules",
    "get_all_rules",
    "get_rule",
    "get_rule_classes",
    "register_rule",
]
