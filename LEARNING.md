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

---

## Phase 2: All 10 Migration Safety Rules (MG001 - MG010), AST Inspection & Fixtures

### 1. Files Created and What Each Does

- **`src/migrationguard/rules/helpers.py`**:
  Common AST inspection functions shared across rules:
  - `get_table_name(stmt)`: Extracts relation name from various statement types (`AlterTableStmt`, `DropStmt`, etc.).
  - `is_table_created_in_same_file(table_name, context)`: Checks if a table was created in a preceding `CREATE TABLE` statement within the same migration file.
  - `has_preceding_lock_timeout(context)`: Checks if a previous statement configured `SET lock_timeout`.
  - `is_alter_table_drop_column(cmd)`: Verifies if an `AlterTableCmd` is dropping a column.

- **Rule Implementations (`src/migrationguard/rules/`)**:
  - `mg001_drop_column_or_table.py` (`MG001` - ERROR): Flags `DROP TABLE` and `ALTER TABLE ... DROP COLUMN` which cause immediate data loss.
  - `mg002_rename_column_or_table.py` (`MG002` - WARNING): Flags table/column renames that break running application versions during rolling deploys.
  - `mg003_add_column_not_null_without_default.py` (`MG003` - ERROR): Flags `ADD COLUMN ... NOT NULL` without a default value, which fails on non-empty tables and locks the relation.
  - `mg004_set_not_null_on_existing_column.py` (`MG004` - WARNING): Flags `ALTER COLUMN SET NOT NULL` which triggers an exclusive table scan.
  - `mg005_create_index_without_concurrently.py` (`MG005` - ERROR): Flags `CREATE INDEX` missing `CONCURRENTLY`, which locks table writes.
  - `mg006_add_foreign_key_without_not_valid.py` (`MG006` - ERROR): Flags foreign keys added without `NOT VALID`, locking both tables for a sequential validation scan.
  - `mg007_add_check_constraint_without_not_valid.py` (`MG007` - WARNING): Flags check constraints added without `NOT VALID`, locking the table during validation.
  - `mg008_alter_column_type.py` (`MG008` - ERROR): Flags column type modifications which trigger full table rewrites and index rebuilds under exclusive lock.
  - `mg009_update_or_delete_without_where.py` (`MG009` - ERROR): Flags `UPDATE` or `DELETE` statements without a `WHERE` clause.
  - `mg010_missing_lock_timeout.py` (`MG010` - WARNING): Flags `ALTER TABLE` statements not preceded by a `SET lock_timeout` statement.

- **Fixtures (`tests/fixtures/`)**:
  - `bad/*.sql`: SQL files containing risky operations (e.g. `mg001_drop_table.sql`, `mg005_create_index_not_concurrent.sql`).
  - `good/*.sql`: Safe migration patterns (e.g. `good_create_index_concurrently.sql`, `good_add_check_not_valid.sql`, `good_new_table_operations.sql`).

- **Tests (`tests/unit/`)**:
  - `test_fixtures.py`: Dynamically discovers and runs tests over all `.sql` files in `fixtures/bad/` (asserts expected rule fires) and `fixtures/good/` (asserts zero findings).
  - `test_rules.py`: Comprehensive unit tests testing each rule's bad cases, safe alternatives, and edge cases (especially tables created in the same migration).

---

### 2. How Data Flows Between Them

```
Migration SQL File
       │
       ▼
 [ parser.py ]
       │
       ▼
 ParsedStatement.stmt  (pglast AST Node)
       │
       ├─────────────────────────────────┐
       ▼                                 ▼
 [ helpers.py ]                    [ Rule.check() ]
 get_table_name()                  Inspects node types:
 is_table_created_in_same_file()     - DropStmt
 has_preceding_lock_timeout()        - AlterTableStmt (AlterTableCmd)
       │                             - IndexStmt (concurrent flag)
       │                             - UpdateStmt / DeleteStmt (whereClause)
       ▼                                 │
 If table is new in this migration:      │
   Skip checks (safe!)                   ▼
       │                          Emits Finding(rule_id, severity, message, suggestion, ...)
       └─────────────────────────────────┘
```

1. **AST Extraction**: `parser.py` parses SQL statements and passes each `ParsedStatement` along with a `StatementContext` to all active rules.
2. **Contextual Exemption**: Each rule first extracts the target table name and calls `is_table_created_in_same_file()`. If the table was created in an earlier statement in the very same migration, the table is brand new and empty, so schema changes on it cannot block production traffic or lose live data.
3. **AST Node Inspection**: The rule inspects AST attributes (such as `cmd.subtype == AlterTableType.AT_AddColumn`, `constraint.contype == ConstrType.CONSTR_NOTNULL`, `index.concurrent == False`).
4. **Finding Generation**: If an unsafe pattern is detected, the rule constructs a structured `Finding` containing a human-readable explanation, line number, SQL snippet, and a concrete safe alternative suggestion.

---

### 3. Key Python Concepts Used

#### Concept 1: ClassVar (`typing.ClassVar`)
When you annotate a variable in a class definition without `ClassVar`, tools like dataclasses or type checkers may treat it as an instance attribute. Annotating with `ClassVar[T]` explicitly tells Python and type checkers that the variable belongs to the class itself, shared across all instances.

```python
from typing import ClassVar


class Rule:
    id: ClassVar[str] = "MG001"
    name: ClassVar[str] = "DROP COLUMN"


print(Rule.id)  # "MG001" (accessed directly on the class)
```

#### Concept 2: Safe Attribute Access with Defaults (`getattr`)
pglast AST nodes have varying attributes depending on statement type (e.g., `AlterTableCmd` might have `def_`, `name`, `subtype`, but not `whereClause`). Using dot notation like `node.whereClause` would raise an `AttributeError` on statements without that attribute. `getattr(node, 'whereClause', None)` safely returns `None` if the attribute doesn't exist.

```python
# Safe navigation on dynamic AST objects:
where_clause = getattr(statement_node, "whereClause", None)
if where_clause is None:
    print("No WHERE clause found!")
```

#### Concept 3: Pytest Parametrization (`@pytest.mark.parametrize`)
Instead of writing 20 nearly identical test functions for 20 fixture files, `@pytest.mark.parametrize` allows you to write a single test function that pytest runs repeatedly with different input parameters, generating a distinct test result for each item.

```python
from pathlib import Path
import pytest

files = list(Path("fixtures").glob("*.sql"))


@pytest.mark.parametrize("file_path", files, ids=[p.name for p in files])
def test_all_fixtures(file_path: Path):
    assert file_path.exists()
```

#### Concept 4: Short-Circuit Evaluation with `any()` and Generator Expressions
When checking whether any constraint in a list is `NOT NULL` or `DEFAULT`, a generator expression with `any()` halts evaluation as soon as the first matching item is found, avoiding unnecessary iterations.

```python
constraints = [c1, c2, c3]
has_not_null = any(getattr(c, "contype", None) == "CONSTR_NOTNULL" for c in constraints)
```

---

### 4. Design Decisions & Interview Questions

#### Q1: Why do all rules check `is_table_created_in_same_file()` to skip newly created tables?
**Answer:** Schema migration rules are designed to prevent blocking production queries, locking existing tables, and causing downtime. When a table is created inside the *same* migration file (e.g. `CREATE TABLE temp_t ...; ALTER TABLE temp_t ADD COLUMN ...;`), that table is brand new, completely empty, and not yet receiving traffic from any running application instances. Therefore, operations like adding columns, creating non-concurrent indexes, or setting NOT NULL on it execute instantaneously with zero locking risk. Skipping new tables eliminates false positives while keeping developer velocity high.

#### Q2: Why does `CREATE INDEX CONCURRENTLY` make index creation safe, and what is its caveat?
**Answer:** A standard `CREATE INDEX` acquires a `SHARE` lock on the table. While read queries (`SELECT`) can continue, all write queries (`INSERT`, `UPDATE`, `DELETE`) are blocked for the full duration of the index build. On large tables, this lock can last minutes or hours, exhausting application connection pools. `CREATE INDEX CONCURRENTLY` builds the index in two passes without acquiring a write lock, allowing concurrent reads and writes. The main caveat is that PostgreSQL does not allow `CREATE INDEX CONCURRENTLY` to run inside an explicit transaction block (`BEGIN ... COMMIT`).

#### Q3: Why does PostgreSQL's `NOT VALID` constraint mechanism prevent table lockouts?
**Answer:** When adding a constraint (such as a foreign key or check constraint) without `NOT VALID`, PostgreSQL immediately acquires an `ACCESS EXCLUSIVE` (or `SHARE ROW EXCLUSIVE`) lock and performs a sequential scan of every row in the table to verify data compliance. By adding the constraint with `NOT VALID`, PostgreSQL acquires a brief lock to register the constraint metadata and enforce it on future writes only, without scanning existing rows. In a subsequent step, `ALTER TABLE ... VALIDATE CONSTRAINT ...` validates existing rows using a weaker lock (`SHARE UPDATE EXCLUSIVE`) that allows concurrent reads and writes.

#### Q4: Why is `SET lock_timeout` critical before executing `ALTER TABLE` statements in production?
**Answer:** In PostgreSQL, lock requests enter a FIFO (First-In, First-Out) queue. When an `ALTER TABLE` command requests an `ACCESS EXCLUSIVE` lock, it must wait for any running read queries on that table to finish. While `ALTER TABLE` waits in the queue, all subsequent queries (even simple `SELECT` statements) must wait *behind* it. A single long-running query can cause all traffic to pile up behind `ALTER TABLE`, quickly consuming all connection pool slots and causing an outage. Setting `SET lock_timeout = '2s'` ensures that if the lock cannot be acquired within 2 seconds, PostgreSQL aborts the migration rather than blocking incoming application traffic.

#### Q5: Why did we test each rule using both fixture `.sql` files and programmatic edge-case unit tests?
**Answer:** We used a two-layer testing strategy:
1. **Fixture files (`tests/fixtures/`)**: Serve as living documentation and realistic end-to-end examples of actual `.sql` files that developers commit. They allow non-Python engineers or DBAs to easily add test cases without writing Python test code.
2. **Programmatic unit tests (`tests/unit/test_rules.py`)**: Exercise specific AST edge cases, such as multiple statements, case insensitivity, newly created tables in the same file, missing clauses, and safe alternative patterns.
Together, they ensure 96%+ test coverage and protect against regressions.

