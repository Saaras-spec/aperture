"""Abstract base class and registry storage for Aperture rules."""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from typing import ClassVar

from aperture.models import Finding, ParsedStatement, Severity, StatementContext

_RULE_REGISTRY: dict[str, type[Rule]] = {}


def register_rule(rule_cls: type[Rule]) -> None:
    """Register a rule class in the global registry."""
    if not hasattr(rule_cls, "id") or not rule_cls.id:
        raise ValueError(f"Rule class {rule_cls.__name__} must define a non-empty 'id'")
    _RULE_REGISTRY[rule_cls.id] = rule_cls


def get_registered_rules() -> dict[str, type[Rule]]:
    """Return a copy of the registered rule classes."""
    return dict(_RULE_REGISTRY)


def clear_rule_registry() -> None:
    """Clear all registered rules (useful in unit tests)."""
    _RULE_REGISTRY.clear()


class Rule(ABC):
    """Abstract base class for all Aperture rules.

    Subclasses must define:
      id: str (e.g. "MG001")
      name: str
      default_severity: Severity
      description: str

    Subclasses must implement:
      check(statement: ParsedStatement, context: StatementContext) -> list[Finding]
    """

    id: ClassVar[str]
    name: ClassVar[str]
    default_severity: ClassVar[Severity]
    description: ClassVar[str]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        # Auto-register concrete subclasses with a defined id
        if getattr(cls, "id", None) and not inspect.isabstract(cls):
            register_rule(cls)

    @abstractmethod
    def check(self, statement: ParsedStatement, context: StatementContext) -> list[Finding]:
        """Check a single parsed statement and return any findings.

        Args:
            statement: The parsed statement to inspect.
            context: Contextual file and migration metadata.

        Returns:
            A list of Finding instances (empty if no violations detected).
        """
        ...
