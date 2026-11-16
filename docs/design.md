<!-- TODO: completed in Phase 5 (Final Design & Documentation) -->
# MigrationGuard Design Document

## Problem Statement
Database migrations are frequently deployed in CI/CD pipelines without zero-downtime safety checks. Operations like dropping columns, altering column types, creating indexes without `CONCURRENTLY`, or adding `NOT NULL` columns without default values can lock tables or cause application downtime and data loss.

## Architecture
MigrationGuard runs a modular pipeline:
1. **Discovery**: Identifies modified SQL migration files from git diff or explicit file globs.
2. **Parser**: Parses PostgreSQL statements into AST nodes using `pglast` (backed by PostgreSQL's actual parser) while computing line numbers from byte locations.
3. **Engine & Rules**: Evaluates enabled rules against each statement context, considering inline suppression comments (`-- migrationguard:ignore`).
4. **Reporters**: Formats findings for console output, PR markdown comments, JSON artifacts, and GitHub code scanning (SARIF).
5. **Exit Code**: Exits non-zero if any finding reaches or exceeds the configured `fail_on` severity threshold.

## Trade-offs & Decisions
- **`pglast` vs regex**: Regex is brittle for SQL dialect nuances, comments, and nested queries. `pglast` provides Postgres's actual AST at the cost of requiring C extensions.
- **Stateless Rules**: Rules are self-contained and auto-discovered, simplifying extensibility.
