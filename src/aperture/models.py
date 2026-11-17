"""Core domain models for Aperture."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aperture.schema import SchemaState


class Severity(StrEnum):
    """Finding severity level."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"

    @property
    def level(self) -> int:
        """Numeric rank for severity comparison: INFO < WARNING < ERROR."""
        levels = {"INFO": 1, "WARNING": 2, "ERROR": 3}
        return levels[self.value]

    def __lt__(self, other: object) -> bool:
        if isinstance(other, Severity):
            return self.level < other.level
        return NotImplemented

    def __le__(self, other: object) -> bool:
        if isinstance(other, Severity):
            return self.level <= other.level
        return NotImplemented

    def __gt__(self, other: object) -> bool:
        if isinstance(other, Severity):
            return self.level > other.level
        return NotImplemented

    def __ge__(self, other: object) -> bool:
        if isinstance(other, Severity):
            return self.level >= other.level
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.value)


@dataclass
class Finding:
    """Represents a risk or policy violation detected in a migration."""

    rule_id: str
    severity: Severity
    message: str
    suggestion: str
    file: str
    line: int
    statement_sql: str
    suppressed: bool = False
    suppression_reason: str | None = None
    explanation: str | None = None
    baselined: bool = False


@dataclass
class ParsedStatement:
    """A single parsed SQL statement with AST and source location details."""

    stmt: Any
    sql: str
    start_line: int
    start_location: int = 0
    raw_stmt: Any = None
    comments: list[str] = field(default_factory=list)

    @property
    def node(self) -> Any:
        """Alias for the underlying AST statement node."""
        return self.stmt

    @property
    def statement_sql(self) -> str:
        """Alias for the SQL string."""
        return self.sql


@dataclass
class StatementContext:
    """Contextual metadata passed to rules during evaluation."""

    file_path: str
    statement_index: int = 0
    all_statements: list[ParsedStatement] = field(default_factory=list)
    large_tables: set[str] = field(default_factory=set)
    schema: SchemaState | None = None
    target_postgres_version: int = 14
