You are a senior platform engineer. We are building "Aperture": an open-source
CLI and GitHub Action that scans PostgreSQL migration files in a pull request, detects
risky operations, and reports findings with safe alternatives.

GOAL
Developers put .sql migration files in a repo. Aperture finds the ones changed in
a PR, parses them with Postgres's real parser, runs a set of rules, and reports risks
as a console report, a markdown PR comment, JSON, and SARIF. It exits non-zero if any
finding at or above the configured fail severity exists.

TECH STACK (do not substitute without asking me)
- Python 3.11+, packaged with pyproject.toml, src/ layout, package name "aperture"
- pglast for parsing SQL (do not use regex to detect risky statements)
- Typer for the CLI, PyYAML + Pydantic v2 for config
- pytest + pytest-cov for tests, ruff for lint/format, mypy (strict) for types
- Docker, GitHub Actions
- No web framework, no database required for unit tests

ARCHITECTURE (pipeline)
input (git diff / paths) -> parser -> rule engine (+ config) -> findings -> reporters -> exit code

REPO STRUCTURE (create exactly this)
aperture/
├── src/aperture/
│   ├── __init__.py
│   ├── cli.py              # Typer app: check, init, list-rules, explain
│   ├── models.py           # Severity enum, Finding dataclass, StatementContext
│   ├── parser.py           # pglast wrapper -> list of ParsedStatement (with line numbers)
│   ├── engine.py           # runs enabled rules over statements, applies suppression + severity overrides
│   ├── config.py           # Pydantic models + YAML loader (aperture.yml)
│   ├── discovery.py        # find migration files: explicit paths, glob, or `git diff --name-only base...HEAD`
│   ├── suppression.py      # parse "-- aperture:ignore <rule-id> <reason>" comments
│   ├── rules/
│   │   ├── __init__.py     # registry: auto-discovers and registers all rules
│   │   ├── base.py         # abstract Rule class
│   │   └── (one file per rule)
│   └── reporters/
│       ├── __init__.py
│       ├── console.py
│       ├── markdown.py
│       ├── json_reporter.py
│       └── sarif.py
├── tests/
│   ├── unit/               # one test file per rule + engine, config, suppression, reporters
│   ├── integration/        # tests that run against a real Postgres (docker), marked @pytest.mark.integration
│   └── fixtures/
│       ├── bad/            # .sql files that must trigger specific rules
│       └── good/           # .sql files that must produce zero findings
├── action.yml              # GitHub Action (Docker-based)
├── Dockerfile
├── docs/design.md          # short design doc: problem, architecture, decisions, trade-offs
├── examples/aperture.yml
├── .github/workflows/ci.yml
├── pyproject.toml
├── LEARNING.md             # explain the code to me (see rules below)
└── README.md

CORE DESIGN RULES
- A Rule is a small class with: id (e.g. "MG001"), name, default_severity, description,
  check(statement, context) -> list[Finding]. Rules never print or format output.
- A Finding is a plain dataclass: rule_id, severity, message, suggestion, file, line, statement_sql.
- Reporters only consume Findings. Rules never import reporters.
- Adding a new rule must require adding exactly one file in rules/ (auto-registered).
- Severity: INFO < WARNING < ERROR. Config has fail_on (default ERROR).
- Config file aperture.yml: enable/disable rules, per-rule severity override,
  fail_on, migration path globs, list of "large_tables" (rules may be stricter for these).
- Inline suppression: a comment "-- aperture:ignore MG001 reason text" on the line
  above a statement suppresses that rule for that statement; reason is mandatory;
  suppressed findings are still reported as "suppressed" in JSON output.
- Line numbers must be accurate: compute from statement byte offsets from pglast.

RULES TO IMPLEMENT (MVP)
MG001 DROP COLUMN / DROP TABLE (data loss) - ERROR
MG002 Rename column or table (breaks running app versions) - WARNING
MG003 ADD COLUMN ... NOT NULL without DEFAULT - ERROR
MG004 ALTER COLUMN SET NOT NULL on existing column (full scan + lock) - WARNING
MG005 CREATE INDEX without CONCURRENTLY - ERROR
MG006 ADD FOREIGN KEY without NOT VALID - ERROR
MG007 ADD CHECK constraint without NOT VALID - WARNING
MG008 ALTER COLUMN TYPE (table rewrite) - ERROR
MG009 UPDATE or DELETE without WHERE - ERROR
MG010 Risky ALTER TABLE with no preceding SET lock_timeout in the same file - WARNING
Each rule must include a clear message and a concrete safe alternative in `suggestion`.

QUALITY BAR
- Type hints everywhere, mypy strict passes, ruff passes.
- Every rule has unit tests for at least: one bad case, one good case, one edge case.
- Test coverage target: 90%+.
- Small, readable functions. Docstrings on every public class/function.
- Never invent pglast APIs: check the installed pglast version's documentation/source
  and verify with a quick script before relying on a node type or attribute.

WORKING STYLE
- Work in phases. After each phase: run ruff, mypy, pytest and show me the results.
- Before coding each phase, give me a short plan. Do not skip ahead to later phases.
- If something is ambiguous, ask me instead of guessing.

LEARNING RULE (important)
I am new to Python. After each phase, append a section to LEARNING.md that explains,
in simple language: what files were created, what each does, how data flows between them,
the 3-5 key Python concepts used (with a tiny example each), and 5 interview-style
questions with answers about the design decisions made in that phase.

Acknowledge you understand, summarize the plan in 10 lines, and wait for "Start Phase 1".