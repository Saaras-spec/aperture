# MigrationGuard Learning Log

Welcome to the MigrationGuard learning companion! This document explains the codebase step-by-step across all development phases.

---

## Phase 1: Foundations, Parser & Rule Registry

### 1. Files Created and What Each Does

- **`pyproject.toml`**: The modern configuration file for packaging Python projects. It defines package metadata, dependencies (`pglast`, `typer`, `pydantic`, `pyyaml`), dev dependencies (`pytest`, `ruff`, `mypy`), entry points, and linter/type-checker settings.
- **`src/migrationguard/models.py`**: The domain data structures of MigrationGuard.
  - `Severity`: An enumeration (`INFO`, `WARNING`, `ERROR`) with custom ordering so `INFO < WARNING < ERROR`.
  - `Finding`: A dataclass representing an issue found by a rule (rule ID, severity, message, suggestion, file, line, SQL snippet, suppression status).
  - `ParsedStatement`: Represents an individual SQL statement parsed by Postgres, storing its AST node, raw SQL text, 1-indexed start line, character offset, and associated comments.
  - `StatementContext`: Metadata provided to rules during inspection (file path, index, all sibling statements, large tables).
- **`src/migrationguard/parser.py`**: The bridge to PostgreSQL's native parser via `pglast`.
  - `ParseError`: Custom exception class capturing syntax error messages along with file path and line number.
  - `parse_sql_string`: Breaks a SQL string into individual `ParsedStatement` objects with line numbers and preceding comments.
  - `parse_sql_file`: Reads a SQL file from disk and parses it.
- **`src/migrationguard/rules/base.py`**:
  - `Rule`: The abstract base class that all migration safety rules inherit from. Defines required attributes (`id`, `name`, `default_severity`, `description`) and the abstract `check(...)` method.
  - Automatically registers any concrete subclass into a global dictionary `_RULE_REGISTRY` via Python's `__init_subclass__`.
- **`src/migrationguard/rules/__init__.py`**:
  - Exposes functions `discover_rules()`, `get_rule(id)`, and `get_all_rules()` to dynamically load rule modules from disk without hardcoded imports.
- **Skeleton Files**:
  - `src/migrationguard/cli.py`: Typer CLI command group (`check`, `init`, `list-rules`, `explain`).
  - `src/migrationguard/engine.py`, `config.py`, `discovery.py`, `suppression.py`: Pipeline module stubs for subsequent phases.
  - `src/migrationguard/reporters/`: Console, Markdown, JSON, and SARIF reporter stubs.
  - `action.yml`, `Dockerfile`, `docs/design.md`, `examples/migrationguard.yml`, `.github/workflows/ci.yml`.
- **Unit Tests**:
  - `tests/unit/test_models.py`: Tests `Severity` ordering and dataclass properties.
  - `tests/unit/test_parser.py`: Tests single, multiple, multiline, commented, empty, and invalid SQL statements, plus UTF-8 multi-byte handling.
  - `tests/unit/test_registry.py`: Tests auto-registration, abstract class exclusion, error handling, and rule discovery.

---

### 2. How Data Flows Between Them

```
SQL String / File on disk
         │
         ▼
[ parser.py ] ──(uses pglast)──> Validates syntax & computes line numbers
         │
         ▼
List of [ ParsedStatement ] ──> Stores AST node, clean SQL, line number, comments
         │
         ▼
[ StatementContext ] ─────────> Packages file path, sibling statements, large tables list
         │
         ▼
[ Rule.check(...) ] ──────────> (Rules auto-registered in rules/base.py)
         │
         ▼
List of [ Finding ] ──────────> Clean dataclasses consumed by future engine & reporters
```

1. **Input**: A migration SQL file is read.
2. **Parsing**: `parser.py` calls `pglast.parse_sql(sql)`. If invalid, a `ParseError` is raised with the exact line number.
3. **Statement Construction**: Slices and AST nodes are wrapped into `ParsedStatement` instances with accurate start lines.
4. **Rule Discovery**: Any rule subclassing `Rule` in `migrationguard.rules` registers itself automatically in `rules/base.py` upon import.
5. **Output**: Rules will inspect `ParsedStatement` in the next phases and emit `Finding` objects.

---

### 3. Key Python Concepts Used

#### Concept 1: Dataclasses (`@dataclass`)
In standard Python classes, writing `__init__`, `__repr__`, and `__eq__` takes a lot of repetitive code. The `@dataclass` decorator automatically generates these methods from type-annotated fields.

```python
from dataclasses import dataclass


@dataclass
class Finding:
    rule_id: str
    message: str
    line: int


# Automatically gives us:
# 1. Constructor: Finding("MG001", "Risky drop", 10)
# 2. Readable representation: Finding(rule_id='MG001', message='Risky drop', line=10)
# 3. Equality: finding1 == finding2 checks field values
```

#### Concept 2: String Enumerations with Operator Overloading (`StrEnum`)
`StrEnum` (introduced in Python 3.11) represents an enumeration where every member is also a string (so `"ERROR" == Severity.ERROR` works seamlessly). To allow severity threshold comparisons like `finding.severity >= fail_on`, we implement comparison "dunder" (double-underscore) methods: `__lt__`, `__le__`, `__gt__`, and `__ge__`.

```python
from enum import StrEnum


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"

    @property
    def level(self) -> int:
        return {"INFO": 1, "WARNING": 2, "ERROR": 3}[self.value]

    def __lt__(self, other: object) -> bool:
        if isinstance(other, Severity):
            return self.level < other.level
        return NotImplemented


print(Severity.ERROR > Severity.WARNING)  # True
print(Severity.INFO < Severity.WARNING)  # True
```

#### Concept 3: Abstract Base Classes (`ABC` & `@abstractmethod`)
An abstract base class defines a contract. It prevents incomplete subclasses from being instantiated and enforces that derived classes implement required methods.

```python
from abc import ABC, abstractmethod


class Rule(ABC):
    id: str

    @abstractmethod
    def check(self, sql: str) -> bool:
        """Subclasses MUST implement this method."""
        ...


# Instantiating a class without implementing check() raises TypeError!
```

#### Concept 4: Class Creation Hooks (`__init_subclass__`)
`__init_subclass__` is called whenever a class inherits from the base class. We use it combined with `inspect.isabstract` to build a plugin-like auto-registering rule architecture: simply creating a concrete rule class automatically registers it without needing a central list!

```python
import inspect
from abc import ABC, abstractmethod

REGISTRY = {}


class Rule(ABC):
    id: str

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if getattr(cls, "id", None) and not inspect.isabstract(cls):
            REGISTRY[cls.id] = cls
```

#### Concept 5: Custom Exceptions with Exception Chaining (`raise ... from err`)
Custom exceptions communicate domain-specific errors cleanly. Python's `from err` syntax preserves the original traceback (the "cause") while presenting our own high-level `ParseError` to callers.

```python
class ParseError(Exception):
    def __init__(self, message: str, file: str, line: int):
        self.file = file
        self.line = line
        super().__init__(f"{file}:{line}: {message}")


try:
    raise ValueError("syntax error at position 42")
except ValueError as e:
    raise ParseError("Invalid SQL", file="001.sql", line=5) from e
```

---

### 4. Design Decisions & Interview Questions

#### Q1: Why use `pglast` (Postgres's real C parser) rather than regular expressions or lightweight SQL tokenizers?
**Answer:** SQL has a notoriously complex grammar. Regular expressions fail on basic edge cases: table or column names inside comments, string literals with semicolons (`'hello; world'`), dollar-quoted multiline strings (`$$ ... $$`), and complex nested subqueries. `pglast` exposes the exact PostgreSQL parser code compiled directly from Postgres C source code. This eliminates parsing false-positives and gives MigrationGuard the identical parse tree that the production database sees.

#### Q2: Why use `__init_subclass__` instead of a decorator or a hardcoded dictionary for the rule registry?
**Answer:** A hardcoded list creates tight coupling: every new rule requires editing a central file, causing merge conflicts in multi-contributor repositories. A decorator (e.g. `@register`) is easy to forget on new rules. `__init_subclass__` guarantees that *every* concrete subclass of `Rule` is automatically registered the moment its module is loaded, satisfying our core architectural requirement: "Adding a new rule must require adding exactly one file in `rules/`".

#### Q3: How are statement line numbers computed, and why does character vs byte encoding matter?
**Answer:** PostgreSQL's underlying C parser operates on raw UTF-8 bytes and reports byte offsets. In Python, strings are sequences of Unicode code points. When SQL migrations contain multi-byte UTF-8 characters (like comments with accents or emojis: `café ☕`), a character offset differs from a byte offset. We compute line numbers by counting newline characters (`\n`) up to the offset in the text (`text[:offset].count("\n") + 1`). Accurate line numbers are vital so PR annotations and GitHub checks flag the exact line where the unsafe statement starts.

#### Q4: Why explicitly define all four comparison methods (`__lt__`, `__le__`, `__gt__`, `__ge__`) on `Severity(StrEnum)` instead of relying on `@total_ordering`?
**Answer:** In Python, `StrEnum` inherits from `str`. `str` already defines its own comparison methods based on alphabetical ASCII order (`'ERROR' < 'WARNING'` is True because `'E' < 'W'`). `@total_ordering` only generates missing comparison methods if the base class does not define them. Because `str` already defines them, `@total_ordering` silently leaves the alphabetical string comparison intact! Explicitly defining `__lt__`, `__le__`, `__gt__`, and `__ge__` guarantees that severity hierarchy (`INFO: 1 < WARNING: 2 < ERROR: 3`) strictly takes precedence.

#### Q5: Why are `Finding` and `ParsedStatement` pure dataclasses that don't depend on CLI or formatting tools?
**Answer:** Following Clean Architecture and Single Responsibility principles, rules and models should have no knowledge of how findings are presented (console colors, markdown tables, JSON payloads, or SARIF files). By keeping `Finding` as a pure data container, rules remain fast, testable in isolation, and decoupled from reporting mechanisms. Reporters simply consume findings and format them.
