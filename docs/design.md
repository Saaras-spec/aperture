# Aperture Design Document

## 1. Problem Statement

Database migrations are a critical step in modern continuous deployment pipelines. However, typical CI/CD checks test only application code (linting, unit tests, integration tests) while executing raw SQL migrations without evaluating operational database risks.

In production PostgreSQL databases under active load:
- Adding a `NOT NULL` column without a `DEFAULT` fails on existing rows and holds an `ACCESS EXCLUSIVE` table lock.
- Creating an index without `CONCURRENTLY` acquires a `SHARE` lock that blocks all incoming writes (`INSERT`, `UPDATE`, `DELETE`) for the entire duration of the build.
- Renaming columns or tables breaks currently running application instances during rolling zero-downtime deployments.
- Dropping tables or columns causes immediate, irreversible data loss.
- An `ALTER TABLE` without a prior `SET lock_timeout` enters a FIFO lock queue behind slow queries and blocks all subsequent incoming queries (including quick `SELECT`s), rapidly exhausting connection pools and causing a site-wide outage.

**Aperture** solves this problem by statically inspecting PostgreSQL migrations in pull requests using PostgreSQL's native parser, flagging risky statements before they reach production, and recommending safe zero-downtime alternatives.

---

## 2. System Architecture

Aperture follows a deterministic, five-stage pipeline:

```
[ Input Sources ]
  - Explicit file paths
  - Git diff (e.g. origin/main...HEAD)
  - Config globs (migrations/**/*.sql)
        │
        ▼
[ 1. Discovery ] ──────> Discovers and filters to unique .sql migration files
        │
        ▼
[ 2. Parser ] ─────────> Parses SQL into ParsedStatement ASTs via pglast
        │               Computes exact source line numbers and extracts comments
        │
        ▼
[ 3. Suppression ] ────> Parses '-- aperture:ignore <RULE_ID> <reason>'
        │               Enforces mandatory justification (missing reason -> MG000)
        │
        ▼
[ 4. Rule Engine ] ────> Evaluates active rules against StatementContext
        │               Exempts statements on newly created tables in same file
        │               Applies config overrides and marks suppressions
        │
        ▼
[ 5. Reporters ] ──────> Transforms List[Finding] into output artifacts:
        │                 - Console (ANSI colored terminal output)
        │                 - Markdown (GitHub PR comment with hidden update marker)
        │                 - JSON (machine-readable report with summary)
        │                 - SARIF 2.1.0 (GitHub Code Scanning inline annotations)
        │
        ▼
[ Process Exit ] ─────> 0 (clean pass), 1 (violations >= fail_on), 2 (error)
```

---

## 3. Core Design Decisions & Trade-Offs

### 3.1. Parsing via `pglast` vs. Regular Expressions
- **Decision**: Use `pglast`, which bundles the genuine PostgreSQL C parser compiled into a Python extension.
- **Alternatives Considered**: Regex pattern matching, `sqlparse` tokenization.
- **Why**: SQL has a notoriously complex context-sensitive grammar. Regular expressions cannot reliably distinguish between a table name in an actual `DROP TABLE` statement and a column name, comment, string literal, or nested subquery.
- **Trade-off**: Requires binary wheels or a C compiler to install, but provides zero false positives on PostgreSQL dialect syntax.

### 3.2. Pure Data Findings Decoupled from Reporters
- **Decision**: Rules emit plain `Finding` dataclass instances. Reporters consume findings without rules knowing about output formats.
- **Alternatives Considered**: Rules formatting their own output or printing directly to stdout.
- **Why**: Keeps rules isolated, fast, and testable without mock terminal streams. Allows findings to be rendered across multiple formats simultaneously (e.g. logging to console while exporting SARIF for GitHub Security).

### 3.3. Auto-Registering Rule Registry via `__init_subclass__`
- **Decision**: Subclassing `Rule` automatically registers concrete rule classes into an in-memory dictionary.
- **Alternatives Considered**: Hardcoded list in `__init__.py`, `@register` decorators.
- **Why**: Satisfies the core architectural principle: adding a new rule requires adding exactly one file in `rules/` without modifying central files or causing merge conflicts.

### 3.4. Mandatory Inline Suppression Justifications
- **Decision**: Inline suppression comments (`-- aperture:ignore MGXXX <reason>`) require a non-empty explanation; missing reasons generate an `MG000` finding and leave the rule active.
- **Alternatives Considered**: Silent bare ignore comments (`-- aperture:ignore`).
- **Why**: Guardrails without mandatory justification foster bad habits. Enforcing reasons creates a visible audit trail directly in the migration history.

### 3.5. Contextual Exemption for Newly Created Tables
- **Decision**: If a table was created earlier in the very same migration file, subsequent schema operations on it are exempt from locking rules.
- **Alternatives Considered**: Flagging all `ALTER TABLE` statements indiscriminately.
- **Why**: A table created in the same migration is brand new and empty, receiving zero live traffic. Flagging operations on new tables creates annoying false positives and reduces developer trust.
