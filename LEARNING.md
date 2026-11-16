# Aperture Learning Log

Welcome to the Aperture learning companion! This document explains the codebase step-by-step across all development phases.

---

## Phase 1: Foundations, Parser & Rule Registry

### 1. Files Created and What Each Does

- **`pyproject.toml`**: The modern configuration file for packaging Python projects. It defines package metadata, dependencies (`pglast`, `typer`, `pydantic`, `pyyaml`), dev dependencies (`pytest`, `ruff`, `mypy`), entry points, and linter/type-checker settings.
- **`src/aperture/models.py`**: The domain data structures of Aperture.
  - `Severity`: An enumeration (`INFO`, `WARNING`, `ERROR`) with custom ordering so `INFO < WARNING < ERROR`.
  - `Finding`: A dataclass representing an issue found by a rule (rule ID, severity, message, suggestion, file, line, SQL snippet, suppression status).
  - `ParsedStatement`: Represents an individual SQL statement parsed by Postgres, storing its AST node, raw SQL text, 1-indexed start line, character offset, and associated comments.
  - `StatementContext`: Metadata provided to rules during inspection (file path, index, all sibling statements, large tables).
- **`src/aperture/parser.py`**: The bridge to PostgreSQL's native parser via `pglast`.
  - `ParseError`: Custom exception class capturing syntax error messages along with file path and line number.
  - `parse_sql_string`: Breaks a SQL string into individual `ParsedStatement` objects with line numbers and preceding comments.
  - `parse_sql_file`: Reads a SQL file from disk and parses it.
- **`src/aperture/rules/base.py`**:
  - `Rule`: The abstract base class that all migration safety rules inherit from. Defines required attributes (`id`, `name`, `default_severity`, `description`) and the abstract `check(...)` method.
  - Automatically registers any concrete subclass into a global dictionary `_RULE_REGISTRY` via Python's `__init_subclass__`.
- **`src/aperture/rules/__init__.py`**:
  - Exposes functions `discover_rules()`, `get_rule(id)`, and `get_all_rules()` to dynamically load rule modules from disk without hardcoded imports.
- **Skeleton Files**:
  - `src/aperture/cli.py`: Typer CLI command group (`check`, `init`, `list-rules`, `explain`).
  - `src/aperture/engine.py`, `config.py`, `discovery.py`, `suppression.py`: Pipeline module stubs for subsequent phases.
  - `src/aperture/reporters/`: Console, Markdown, JSON, and SARIF reporter stubs.
  - `action.yml`, `Dockerfile`, `docs/design.md`, `examples/aperture.yml`, `.github/workflows/ci.yml`.
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
4. **Rule Discovery**: Any rule subclassing `Rule` in `aperture.rules` registers itself automatically in `rules/base.py` upon import.
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
**Answer:** SQL has a notoriously complex grammar. Regular expressions fail on basic edge cases: table or column names inside comments, string literals with semicolons (`'hello; world'`), dollar-quoted multiline strings (`$$ ... $$`), and complex nested subqueries. `pglast` exposes the exact PostgreSQL parser code compiled directly from Postgres C source code. This eliminates parsing false-positives and gives Aperture the identical parse tree that the production database sees.

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

- **`src/aperture/rules/helpers.py`**:
  Common AST inspection functions shared across rules:
  - `get_table_name(stmt)`: Extracts relation name from various statement types (`AlterTableStmt`, `DropStmt`, etc.).
  - `is_table_created_in_same_file(table_name, context)`: Checks if a table was created in a preceding `CREATE TABLE` statement within the same migration file.
  - `has_preceding_lock_timeout(context)`: Checks if a previous statement configured `SET lock_timeout`.
  - `is_alter_table_drop_column(cmd)`: Verifies if an `AlterTableCmd` is dropping a column.

- **Rule Implementations (`src/aperture/rules/`)**:
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

---

## Phase 3: Configuration, Inline Suppression, File Discovery & Rule Engine

### 1. Files Created/Completed and What Each Does

- **`src/aperture/config.py`**:
  - `Config`, `RuleConfig`: Pydantic v2 models validating configuration settings (`fail_on`, `migration_paths`, `large_tables`, and `rules`). Extra fields are strictly forbidden (`extra="forbid"`) to catch typos.
  - `load_config`, `load_config_from_yaml`: Parses YAML files via PyYAML, validates data schemas, and formats helpful error messages that explicitly name offending fields. Automatically discovers `aperture.yml` in the current working directory.
- **`src/aperture/suppression.py`**:
  - `parse_suppressions`: Scans statement comments for inline suppression directives in the format `-- aperture:ignore <RULE_ID> <reason>`.
  - Enforces mandatory justification: if an ignore directive omits a reason, it emits an `MG000` finding (severity `WARNING`) and refuses to suppress the rule.
- **`src/aperture/engine.py`**:
  - `check_statements`, `check_file`, `check_files`: The pipeline coordinator that takes parsed statements and configuration, evaluates active rules, applies per-rule severity overrides, and tags suppressed findings (`suppressed=True`, `suppression_reason=...`).
  - `compute_exit_code`: Determines CLI exit status (0 for clean pass or only suppressed findings, 1 if any unsuppressed finding meets or exceeds the `fail_on` threshold).
- **`src/aperture/discovery.py`**:
  - `discover_files`: Unified file locator supporting three discovery modes: explicit file/directory paths, `git diff --name-only <base>...HEAD`, and glob patterns from configuration.
  - Filters strictly for existing `.sql` files and provides helpful diagnostic errors when `git` is missing or an invalid base ref is given.
- **`examples/aperture.yml`**:
  - A reference configuration template explaining every configurable setting (`fail_on`, `migration_paths`, `large_tables`, per-rule overrides).
- **Unit & End-to-End Tests**:
  - `tests/unit/test_config.py`: Tests default values, YAML parsing, invalid severities, bad fields, and fallback discovery.
  - `tests/unit/test_suppression.py`: Tests single, multiple, block comment suppressions, and missing-reason `MG000` warnings.
  - `tests/unit/test_engine.py`: Tests rule filtering, severity overrides, inline suppressions, and exit code logic.
  - `tests/unit/test_discovery.py`: Tests path discovery, glob matching, git diff parsing, and error handling.
  - `tests/unit/test_engine_e2e.py`: End-to-end integration tests running rules against fixtures and verifying exit codes.

---

### 2. How Data Flows Between Them

```
CLI / Input Args (Paths, globs, or git diff base)
                 │
                 ▼
     [ discovery.py: discover_files() ]
                 │
                 ▼  List of Path objects (.sql files)
     [ parser.py: parse_sql_file() ]
                 │
                 ▼  List of ParsedStatement objects (AST nodes + comments)
     [ suppression.py: parse_suppressions() ]
                 │
                 ├──────────────────────────────┐
                 ▼                              ▼
          suppressions map                MG000 findings
        (rule_id -> reason)           (missing reasons warning)
                 │                              │
                 ▼                              │
     [ config.py: Config ]                      │
     (Active rules, severity overrides)         │
                 │                              │
                 ▼                              ▼
     [ engine.py: check_statements() ] ◄────────┘
                 │
                 ▼  Applies severity overrides & tags suppressed=True
         List of Finding objects
                 │
                 ▼
     [ engine.py: compute_exit_code(findings, fail_on) ]
                 │
                 ▼
         Exit Code: 0 (Pass) or 1 (Fail)
```

---

### 3. Key Python Concepts Used

#### Concept 1: Pydantic v2 Models & Field Validators (`@field_validator`)
Pydantic v2 allows defining strongly typed, validated configuration schemas. Field validators intercept raw input (like case-insensitive severity strings like `"error"` or `"warning"`) and validate or coerce them before constructing the model.

```python
from pydantic import BaseModel, field_validator


class Settings(BaseModel):
    fail_on: str

    @field_validator("fail_on", mode="before")
    @classmethod
    def normalize_severity(cls, v: str) -> str:
        v_upper = v.upper()
        if v_upper not in ("INFO", "WARNING", "ERROR"):
            raise ValueError(f"Invalid level '{v}'")
        return v_upper
```

#### Concept 2: Forbidding Extra Configuration Keys (`extra = "forbid"`)
By default, Pydantic ignores extra keys in input dictionaries. In configuration files, typos like `fail_upon: ERROR` would be silently ignored, causing unexpected behavior. Setting `extra="forbid"` ensures that any misspelled option immediately halts with a descriptive error.

```python
from pydantic import BaseModel


class Config(BaseModel):
    model_config = {"extra": "forbid"}
    fail_on: str = "ERROR"


# Config(fail_upon="WARNING") raises ValidationError immediately!
```

#### Concept 3: Subprocess Execution with Output Capture (`subprocess.run`)
When integrating with command-line tools like `git diff`, Python's `subprocess.run` provides safe process invocation without shell injection risks. Setting `capture_output=True` and `text=True` automatically captures `stdout` and `stderr` as Python strings.

```python
import subprocess

proc = subprocess.run(
    ["git", "diff", "--name-only", "main...HEAD"],
    capture_output=True,
    text=True,
    check=False,
)
if proc.returncode == 0:
    changed_files = proc.stdout.splitlines()
```

#### Concept 4: Regular Expression Named Capture Groups (`(?P<name>...)`)
Named capture groups make regular expressions readable and maintainable by allowing you to extract matched substrings by descriptive names instead of numeric indices (`match.group("rule_id")`).

```python
import re

pattern = re.compile(r"-- aperture:ignore\s+(?P<rule_id>\w+)\s+(?P<reason>.+)")
match = pattern.match("-- aperture:ignore MG001 Dropping legacy table")
if match:
    print(match.group("rule_id"))  # "MG001"
    print(match.group("reason"))  # "Dropping legacy table"
```

---

### 4. Design Decisions & Interview Questions

#### Q1: Why is an inline suppression reason mandatory, producing an `MG000` finding when missing?
**Answer:** In production engineering, suppressing a safety check without documenting *why* creates hidden technical debt and defeats the purpose of automated guardrails. By requiring a mandatory explanation, teams ensure an audit trail directly in the SQL file (e.g. "Dropping legacy column after verifying zero app queries in release v2.4"). If an engineer simply writes `-- aperture:ignore MG001`, Aperture refuses to suppress the rule and flags an `MG000` warning, enforcing team accountability.

#### Q2: Why does `compute_exit_code` ignore suppressed findings even if their severity exceeds `fail_on`?
**Answer:** Suppressed findings represent deliberate, DBA-reviewed exceptions to safety rules. If suppressed findings still triggered a non-zero exit code, developers would have no way to merge intentional migrations without altering global CI settings. However, suppressed findings are still included in the final report (marked with `suppressed=True` and their reason) so that pull request reviewers and auditors have full visibility into every exception granted.

#### Q3: Why support both explicit file paths, config globs, and `git diff` for file discovery?
**Answer:** Different operating contexts require different discovery modes:
1. **Local CLI (`aperture check path/to/001.sql`)**: Engineers developing a single new migration want to test only that specific file.
2. **Pre-commit / CI on PRs (`git diff --name-only origin/main...HEAD`)**: In a large repository with hundreds of historical migrations, re-analyzing old migrations on every commit is slow and noisy; scanning only the changed files in the PR ensures fast, targeted feedback.
3. **Full audit / Scheduled scans (`config.migration_paths`)**: Teams auditing their whole codebase need glob discovery to find all migrations across all microservice directories.

#### Q4: Why forbid unknown/extra fields in the configuration schema?
**Answer:** Configuration errors frequently happen due to subtle typos—such as writing `rules_override` instead of `rules`, or `failon` instead of `fail_on`. If a parser silently ignores unrecognized fields, developers believe their configuration is active when it is actually doing nothing. By enforcing `extra="forbid"` in Pydantic, Aperture fails fast with a clear error pointing out the exact unrecognized field name.

#### Q5: Why does `engine.py` decouple rule execution from exit code computation and reporting?
**Answer:** In the Aperture architecture, `check_statements()` is a pure domain function: it takes parsed statements and configuration, evaluates rules, and returns plain `Finding` data objects. `compute_exit_code()` is a separate calculation that determines process status. Separating execution from exit code computation allows the same engine to be used by the CLI (which exits 0 or 1), a GitHub Action (which might annotate PRs without failing the build), or a Python API/library without coupling the core analysis to OS process termination.

---

## Phase 4: CLI Application & Multi-Format Reporters

### 1. Files Created/Completed and What Each Does

- **`src/aperture/cli.py`**:
  - Full-featured command-line application built with Typer exposing four user commands:
    - `check`: Scans migration files via explicit paths, `git diff` against a base ref, or config globs. Evaluates rules, renders formatted reports, writes to stdout or file, and exits with 0 (clean pass), 1 (violations at or above `fail_on`), or 2 (bad configuration, invalid options, or SQL parse errors).
    - `list-rules`: Displays a clean catalog table of all registered rules, default severities, and descriptions.
    - `init`: Generates a starter `aperture.yml` configuration file with sensible defaults, refusing to overwrite if one already exists.
    - `explain <rule_id>`: Shows in-depth educational details for a rule: description, why the Postgres operation is dangerous (locks, table rewrites, connection pool stalls), and concrete safe alternatives.
- **`src/aperture/reporters/`**:
  - `__init__.py`: Provides `render_report()`, a single polymorphic dispatch interface that takes findings and a format string (`console`, `markdown`, `json`, `sarif`) and delegates to the appropriate formatter.
  - `console.py`: Colorized terminal reporter that groups findings by file with line numbers, severity badges (`[ERROR]`, `[WARNING]`, `[INFO]`, `[SUPPRESSED]`), statement SQL snippets, safe suggestions, and summary statistics.
  - `markdown.py`: PR comment reporter starting with the hidden marker `<!-- aperture -->`, featuring a summary table, status badge, and collapsible `<details>` blocks for each finding.
  - `json_reporter.py`: Machine-readable JSON output containing full finding attributes and summary counts.
  - `sarif.py`: Generates OASIS SARIF 2.1.0 output compatible with GitHub Code Scanning, complete with driver rule metadata, file URIs, line numbers, and in-source suppressions.
- **Unit & Snapshot Tests**:
  - `tests/unit/test_reporters.py`: Snapshot tests for all four reporters, validating markdown hidden markers, JSON summaries, and full SARIF 2.1.0 schema compliance.
  - `tests/unit/test_cli.py`: End-to-end CLI tests using Typer's `CliRunner` validating all commands, options (`--config`, `--base-ref`, `--format`, `--output`), and exit codes (0, 1, 2).

---

### 2. How Data Flows Between Them

```
User runs CLI: aperture check --format markdown --output pr_comment.md
                                   │
                                   ▼
 [ cli.py: check() ] ──> Loads config.py & discovers files via discovery.py
                                   │
                                   ▼
 [ engine.py: check_files() ] ──> Evaluates rules across all migration statements
                                   │
                                   ▼
 List of [ Finding ] ──> [ reporters: render_report(findings, "markdown") ]
                                   │
                                   ├──────────────────────────────┐
                                   ▼                              ▼
                 Writes to file or stdout             [ compute_exit_code() ]
                 (e.g. pr_comment.md)                 Evaluates fail_on threshold
                                                                  │
                                                                  ▼
                                                      Process Exit Code (0, 1, 2)
```

---

### 3. Key Python Concepts Used

#### Concept 1: Modern Type Annotations with `typing.Annotated`
In modern Python CLI libraries like Typer and FastAPI, `Annotated[T, Metadata]` allows separating the Python data type (`list[Path] | None`) from framework metadata (`typer.Argument(...)` or `typer.Option(...)`). This avoids function default value evaluation issues while keeping type checkers happy.

```python
from pathlib import Path
from typing import Annotated
import typer


def run(
    path: Annotated[Path, typer.Argument(help="File path to process")],
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    pass
```

#### Concept 2: CLI Testing with Isolated Runner (`typer.testing.CliRunner`)
`CliRunner` allows invoking command-line apps programmatically inside unit tests without spawning external subprocesses. It captures `stdout`, `stderr`, and the exit code directly.

```python
from typer.testing import CliRunner

runner = CliRunner()
result = runner.invoke(app, ["explain", "MG001"])
assert result.exit_code == 0
assert "DROP TABLE" in result.output
```

#### Concept 3: Polymorphic Dispatch via Factory Function (`render_report`)
Instead of cluttering CLI logic with `if format == "json": ... elif format == "sarif": ...`, the reporters package encapsulates all formatting strategies behind a single entry point `render_report()`. Adding a new format only requires adding a file in `reporters/` and registering it in `render_report()`.

```python
def render_report(findings: list[Finding], format_name: str) -> str:
    dispatch = {
        "json": format_json,
        "markdown": format_markdown,
        "sarif": format_sarif,
        "console": format_console,
    }
    if format_name not in dispatch:
        raise ValueError(f"Unknown format: {format_name}")
    return dispatch[format_name](findings)
```

#### Concept 4: Standardized Interchange Formats (JSON & SARIF 2.1.0)
SARIF (Static Analysis Results Interchange Format) is a JSON-based OASIS standard for static analysis tools. By generating valid SARIF 2.1.0, Aperture can upload findings directly to GitHub Security Code Scanning (`github/codeql-action/upload-sarif`), displaying annotations directly on PR file diffs.

```python
sarif_doc = {
    "version": "2.1.0",
    "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
    "runs": [{"tool": {"driver": {"name": "Aperture"}}, "results": []}],
}
```

---

### 4. Design Decisions & Interview Questions

#### Q1: Why use specific three-tier CLI exit codes (0, 1, 2) instead of standard binary 0 vs 1?
**Answer:** Standard POSIX conventions distinguish between finding violations and operational failures:
- **Exit 0 (Success)**: Analysis ran cleanly and found no unsuppressed violations at or above the `fail_on` threshold.
- **Exit 1 (Policy Failure)**: The tool executed successfully, but found risky migrations that violate team safety policy (e.g., dropping a table). CI pipelines use this exit code to block the PR from merging.
- **Exit 2 (System / Usage Error)**: The tool failed to run because of invalid arguments, non-existent files, malformed YAML configuration, or SQL syntax errors.
This distinction prevents CI pipelines from confusing a broken config or syntax bug with a lint failure.

#### Q2: Why must the Markdown reporter output begin with the hidden comment `<!-- aperture -->`?
**Answer:** In automated CI workflows (like GitHub Actions), every new commit pushed to a pull request runs Aperture. If the action posted a brand new PR comment on every commit, the PR conversation would become flooded with duplicate bot comments. By prefixing the comment with `<!-- aperture -->`, subsequent workflow runs can query the GitHub Comments API, find the existing comment containing this unique marker, and update it in-place using `PATCH /repos/{owner}/{repo}/issues/comments/{comment_id}`.

#### Q3: Why separate the CLI interface from the reporter implementations?
**Answer:** Following the Single Responsibility Principle, `cli.py` is only responsible for parsing command-line flags, handling process errors, and writing output. Reporters are pure formatting functions (`list[Finding] -> str`). This separation allows reporters to be tested independently in unit tests without CLI dependencies, and allows other integrations (such as a GitHub Action or Python library) to import reporters directly.

#### Q4: Why does Aperture implement SARIF 2.1.0 rather than only custom JSON?
**Answer:** While custom JSON is easy to parse with simple scripts, SARIF 2.1.0 is the industry standard supported natively by GitHub, GitLab, and Azure DevOps. GitHub Code Scanning ingests SARIF to automatically render inline annotations directly on the exact lines of code in the PR diff, create security alerts, and track remediation over time without writing custom comment-posting glue code.

#### Q5: Why does `aperture init` refuse to overwrite existing configuration files?
**Answer:** Safe defaults protect user data. If a developer accidentally runs `aperture init` inside an established project with custom table exemptions and rule severity overrides, silently overwriting `aperture.yml` would erase their customized configuration. Refusing to overwrite and returning an error code forces intentionality.

---

## Phase 5: Containerization, GitHub Actions, Automated PR Comments & CI/CD

### 1. Files Created/Completed and What Each Does

- **`Dockerfile`**:
  - Production-ready, security-hardened container based on `python:3.12-slim`.
  - Creates a dedicated non-root user and group (`appuser`, UID 1000) and switches to it.
  - Installs `aperture` and configures `ENTRYPOINT ["aperture"]` for direct CLI usage.
- **`action.yml`**:
  - Docker-based GitHub Action specification declaring inputs (`config`, `base-ref`, `fail-on`, `github-token`, `comment-on-pr`) and outputs (`findings-count`, `passed`).
  - Mounts repository code into the container and invokes `/app/entrypoint.sh`.
- **`src/aperture/github_comment.py`**:
  - Connects to GitHub's REST API (`/repos/{owner}/{repo}/issues/{pr}/comments`).
  - Searches existing comments for the unique tracking marker `<!-- aperture -->`.
  - Uses `PATCH` to update existing comments or `POST` to create a new comment if none exists.
  - Enforces strict token privacy: tokens are never printed, logged, or included in error messages.
- **`src/aperture/action_runner.py` & `entrypoint.sh`**:
  - The bridge between GitHub Actions and Aperture: parses `INPUT_*` environment variables, executes migration scanning, writes outputs to `$GITHUB_OUTPUT`, prints reports, and posts PR comments.
- **`.github/workflows/ci.yml`**:
  - Continuous Integration pipeline testing every push and PR: runs `ruff` lint and format checks, `mypy` strict type checking, `pytest` unit test suite with coverage, and verifies Docker image builds.
- **`.github/workflows/self-test.yml`**:
  - Automated integration testing for the GitHub Action itself: executes against `tests/fixtures/good/` (verifying success) and `tests/fixtures/bad/` (verifying failure detection).
- **`docs/design.md`**:
  - Full system design document detailing problem statement, architectural pipeline, and design trade-offs.
- **`README.md`**:
  - Complete user-facing documentation covering quick start, CLI commands, GitHub Action configuration, rules catalog, and inline suppressions.
- **Unit Tests**:
  - `tests/unit/test_github_comment.py`: Fully mocked HTTP tests verifying comment discovery, creation, updates, and token sanitization.
  - `tests/unit/test_action_runner.py`: Tests action inputs, outputs, and PR commenting logic.

---

### 2. How Data Flows Between Them

```
Developer opens / updates a Pull Request
                    │
                    ▼
       [ GitHub Action: action.yml ] ──(Docker: python:3.12-slim)
                    │
                    ▼
       [ action_runner.py / entrypoint.sh ]
                    │
                    ├───────────────────────────────┐
                    ▼                               ▼
       [ discover_files() ]               [ load_config() ]
       (git diff origin/main...HEAD)       (aperture.yml)
                    │                               │
                    └───────────────┬───────────────┘
                                    │
                                    ▼
                        [ check_files() ]
                                    │
                                    ▼
                            List of Findings
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
          Writes $GITHUB_OUTPUT             [ github_comment.py ]
        findings-count=X, passed=bool       Finds <!-- aperture -->
                    │                       Updates or posts PR comment
                    ▼                               │
          Console Log Output                        ▼
                    │                       GitHub PR Comment Updated!
                    ▼
          Exit Code (0 or 1)
```

---

### 3. Key Python Concepts Used

#### Concept 1: REST API Requests via Standard Library (`urllib.request`)
Using `urllib.request` allows performing authenticated HTTP calls without introducing third-party runtime dependencies like `requests` or `httpx`, keeping the Docker image and package footprint lean.

```python
import json
import urllib.request

req = urllib.request.Request(
    "https://api.github.com/repos/owner/repo/issues/1/comments",
    data=json.dumps({"body": "Hello"}).encode("utf-8"),
    headers={"Authorization": "Bearer token", "Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode("utf-8"))
```

#### Concept 2: Mocking Network I/O in Unit Tests (`monkeypatch.setattr`)
Unit tests must never depend on live external network services. By mocking `urllib.request.urlopen`, tests run instantaneously and deterministically offline.

```python
import urllib.request
import pytest


def test_api(monkeypatch: pytest.MonkeyPatch):
    class FakeResponse:
        def read(self):
            return b'{"id": 123}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda req: FakeResponse())
```

#### Concept 3: GitHub Actions Step Outputs Contract (`$GITHUB_OUTPUT`)
GitHub Actions uses an environment file pointed to by `$GITHUB_OUTPUT`. Steps write output variables by appending `key=value\n` to this file, making them available to downstream workflow steps.

```python
import os

output_file = os.getenv("GITHUB_OUTPUT")
if output_file:
    with open(output_file, "a", encoding="utf-8") as f:
        f.write("passed=true\n")
```

#### Concept 4: Credential Masking in Exceptions
When interacting with security tokens, exception handlers must never include raw authorization headers or sensitive inputs in error strings, logs, or tracebacks.

```python
try:
    make_api_call(token)
except Exception as err:
    # Safely communicate failure without leaking the secret token!
    raise RuntimeError("API request failed due to authentication error.") from None
```

---

### 4. Design Decisions & Interview Questions

#### Q1: Why use Python's standard library `urllib.request` instead of `requests` or `httpx` for PR commenting?
**Answer:** Minimizing runtime dependencies is a core platform engineering best practice. Adding `requests` or `httpx` introduces transitive dependencies (e.g., `certifi`, `idna`, `charset-normalizer`, `anyio`), increasing attack surface, Docker image build times, and supply chain vulnerability risks. Because GitHub comment management requires only two simple endpoints (`GET` and `POST`/`PATCH`), Python's built-in `urllib.request` handles the task cleanly with zero extra dependencies.

#### Q2: Why run as a dedicated non-root user (`USER appuser`) in the Dockerfile?
**Answer:** Running containers as `root` (UID 0) is a major container security risk. If a container breakout vulnerability occurs, a root process inside the container can gain root privileges on the underlying host system. Creating `appuser` (UID 1000) and switching to it adheres to the Principle of Least Privilege, preventing unauthorized filesystem modifications on the host.

#### Q3: Why post or update a single PR comment using `<!-- aperture -->` rather than creating a new comment on every commit?
**Answer:** Pull requests often receive many commits during code review. If CI posted a new comment for every push, the PR discussion would be flooded with spam, drowning out reviewer feedback. By prefixing the comment with a hidden HTML comment marker `<!-- aperture -->`, subsequent CI runs locate the existing comment and update it in-place via `PATCH`, keeping the PR thread clean.

#### Q4: How does the self-test workflow verify the GitHub Action without infinite recursion?
**Answer:** The self-test workflow (`.github/workflows/self-test.yml`) tests the action locally using `uses: ./` on Ubuntu runners. It tests two explicit cases:
1. Running against `tests/fixtures/good/` and asserting `outcome == 'success'`.
2. Running against `tests/fixtures/bad/` with `continue-on-error: true` and asserting `outcome == 'failure'`.
Testing both positive and negative cases guarantees the action fails when risky migrations are present and succeeds when migrations are safe.

#### Q5: Why does the Docker container use `python:3.12-slim` instead of Alpine Linux?
**Answer:** While Alpine Linux creates smaller raw base images, it uses `musl libc` instead of `glibc`. Python packages that contain C extensions (such as `pglast`, which compiles PostgreSQL's C parser) must compile from source on Alpine, requiring build tools (`gcc`, `make`, `musl-dev`) that bloat the build time and final image. Debian-based `python:3.12-slim` uses `glibc` and supports pre-compiled `manylinux` binary wheels, making `pip install` instantaneous.






---

## Phase 6: End-to-End Demo, Live PostgreSQL Integration Tests & PyPI Packaging

### 1. Files Created and Updated

- **`demo/`**: A self-contained sample repository demonstrating Aperture in action:
  - `demo/db/migrations/001_safe.sql`: Safe migration with `CONCURRENTLY`, `SET lock_timeout`, and non-blocking `ADD COLUMN ... DEFAULT`.
  - `demo/db/migrations/002_unsafe.sql`: Dangerous migration triggering `MG001` (`DROP COLUMN`) and `MG005` (`CREATE INDEX` without `CONCURRENTLY`).
  - `demo/aperture.yml`: Project configuration setting `fail_on: ERROR` and targeting `db/migrations/*.sql`.
  - `demo/.github/workflows/aperture.yml`: Production GitHub Action workflow running on PRs to post diagnostic comments.
  - `demo/README.md`: Step-by-step tutorial explaining how to simulate blocked pull requests.
- **`docker-compose.yml`**: Compose configuration providing a reproducible PostgreSQL 16 container with healthchecks for integration testing.
- **`tests/integration/conftest.py`**: Integration fixtures that discover the PostgreSQL connection URL, perform a healthcheck, and cleanly skip tests if live PostgreSQL is unavailable.
- **`tests/integration/test_postgres_behaviour.py`**: Live database test suite marked `@pytest.mark.integration` proving rules `MG003`, `MG004`, `MG005`, and `MG006` match actual PostgreSQL locking and failure behavior.
- **`.github/workflows/ci.yml`**: Added a dedicated `integration` workflow job using a GitHub Actions PostgreSQL 16 service container.
- **`pyproject.toml`**: Configured `addopts = "-v --strict-markers -m 'not integration'"` to exclude integration tests from default test runs; added `psycopg[binary]` and `build` to dev dependencies.
- **`README.md`**: Added CI, coverage, Python version, and license badges, an example CLI console output block, and a "Try the Demo" guide.
- **`dist/`**: Built release artifacts `aperture_db-0.1.0.tar.gz` (sdist) and `aperture_db-0.1.0-py3-none-any.whl` (wheel) using `python -m build`.

---

### 2. How Data Flows

```
Developer / CI
      │
      ├──> pytest (default) ─────────> tests/unit/ (154 tests, runs in < 2.5s offline)
      │
      ├──> pytest -m integration ────> tests/integration/ (Live PostgreSQL 16)
      │                                       │
      │                                       ├──> Connects via psycopg
      │                                       ├──> Simulates concurrent transactions & locks
      │                                       └──> Asserts NotNullViolation & LockNotAvailable
      │
      └──> python -m build ──────────> PEP 517 build frontend ──> sdist & wheel in dist/
```

1. **Unit Testing Flow:** Running `pytest` automatically filters out integration tests (`-m 'not integration'`), guaranteeing lightning-fast feedback (< 2.5 seconds) during local development without requiring external database dependencies.
2. **Integration Flow:** Running `pytest -m integration` connects to a PostgreSQL instance specified by `POSTGRES_TEST_URL`. Sessions create test tables, apply locks, execute DDL statements, and assert that PostgreSQL raises the exact operational errors (e.g., `NotNullViolation`, `LockNotAvailable`) that Aperture's rules are designed to prevent.
3. **Packaging Flow:** `python -m build` reads metadata and entry points from `pyproject.toml`, builds the source distribution (`.tar.gz`) and platform-independent binary wheel (`.whl`), and outputs them to `dist/`.

---

### 3. Key Python Concepts Used

#### Concept 1: Custom Pytest Markers & Test Filtering (`@pytest.mark`)
Pytest allows tagging tests with custom markers to categorize suites (e.g., unit vs. integration vs. performance). By configuring `addopts` in `pyproject.toml`, we can establish default filtering rules.

```python
import pytest

# Tag all tests in this module with the 'integration' marker
pytestmark = pytest.mark.integration


def test_real_database():
    # Only runs when invoked with: pytest -m integration
    assert True
```

#### Concept 2: Live Database Concurrency & Lock Testing (`psycopg`)
Testing relational database locks requires managing multiple simultaneous database connections (`session1` and `session2`) across independent transactions to prove locking conflicts.

```python
import psycopg

with (
    psycopg.connect("postgresql://user:pass@localhost:5432/db") as s1,
    psycopg.connect("postgresql://user:pass@localhost:5432/db") as s2,
):
    s1.execute("BEGIN; LOCK TABLE users IN SHARE MODE;")
    # s2 will be blocked if it attempts an operation conflicting with SHARE MODE
```

#### Concept 3: Dynamic Test Skipping (`pytest.skip`)
When a test depends on an external service (like a database or Docker daemon) that might not be running in every local development environment, `pytest.skip()` dynamically skips the test with a descriptive reason rather than failing.

```python
import pytest


def check_service_available() -> bool:
    return False


@pytest.fixture
def db():
    if not check_service_available():
        pytest.skip("External service is not reachable on this machine.")
```

#### Concept 4: Modern PEP 517 Packaging (`python -m build`)
Python packaging uses `pyproject.toml` (PEP 518/621) as the universal build specification. The `build` tool is the modern standard frontend that invokes the underlying build backend (e.g., `setuptools.build_meta`) to produce standardized distributions.

```python
# To inspect build metadata programmatically
from importlib.metadata import version

pkg_version = version("aperture-db")
print(f"Installed aperture-db version: {pkg_version}")
```

---

### 4. Design Decisions & Interview Questions

#### Q1: Why separate unit tests and integration tests with pytest markers instead of running all tests together?
**Answer:** Speed and developer ergonomics. Unit tests execute entirely in-memory and parse ASTs in ~2 seconds, allowing developers to run `pytest` continuously on every save. Integration tests require spinning up a live PostgreSQL container, acquiring network sockets, establishing TCP connections, and simulating lock timeouts. By tagging integration tests with `@pytest.mark.integration` and excluding them from default runs via `addopts = "-m 'not integration'"`, developers get sub-second local test feedback while CI runs both suites independently.

#### Q2: Why test lock contention with `LOCK TABLE ... IN SHARE MODE` and `lock_timeout` rather than spawning background processes?
**Answer:** Determinism and execution speed. Simulating an actual index build on a massive table in CI requires generating millions of synthetic rows and waiting seconds or minutes for a background thread to build the index. In PostgreSQL's internal lock manager, `CREATE INDEX` acquires a `SHARE` lock on the relation. By acquiring `SHARE` mode explicitly in Session 1 and configuring a short `lock_timeout = '300ms'` in Session 2, we deterministically test PostgreSQL's lock compatibility matrix in milliseconds without flakiness or resource-intensive data generation.

#### Q3: Why does `CREATE INDEX CONCURRENTLY` require `autocommit=True` and cannot run inside an explicit transaction block?
**Answer:** `CREATE INDEX CONCURRENTLY` works by performing multiple sequential passes over the table: first registering the index in system catalogs, waiting for all existing write transactions to terminate, scanning the table to build the index, and performing a second validation scan. Because it must commit catalog changes and wait for other transactions to end across distinct transaction snapshots, PostgreSQL strictly prohibits `CONCURRENTLY` from running inside an explicit `BEGIN ... COMMIT` block (`ERROR: 25001: CREATE INDEX CONCURRENTLY cannot run inside a transaction block`).

#### Q4: Why provide a `/demo` folder with both safe and unsafe migrations inside the same repository?
**Answer:** Developer adoption and onboarding. A tool that flags risky migrations is only as effective as the developer's ability to understand what "good" and "bad" migrations look like in practice. The `/demo` folder provides a turnkey, copy-pasteable reference project containing real migration files, an `aperture.yml` configuration, and a GitHub Action workflow. Engineers evaluating the tool can test commands directly (`aperture check demo/db/migrations/001_safe.sql`) and see immediate results before configuring their own repositories.

#### Q5: Why build PyPI packages with `python -m build` (PEP 517) instead of legacy `python setup.py sdist bdist_wheel`?
**Answer:** `python setup.py` is deprecated by the Python Packaging Authority (PyPA). Directly executing `setup.py` runs arbitrary code in the developer's shell environment and tightly couples packaging to `setuptools`. PEP 517 and PEP 518 decouple build frontends (like `build` or `pip`) from build backends (like `setuptools`, `flit`, or `hatchling`). Using `python -m build` standardizes the build process, respects declarative `pyproject.toml` configuration, and produces standard source archives and wheels compatible with all modern Python tooling.
