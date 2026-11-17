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

### 3.6. Optional AI Explainer Architecture & Security Model
- **Decision**: Put the AI explainer behind a `typing.Protocol` (`ExplainerProvider`) supporting Anthropic Claude and Google Gemini via standard HTTP requests, enabled via `--explain`.
- **Alternatives Considered**: Direct tightly-coupled SDK imports (`anthropic` / `google-genai`), mandatory LLM analysis for all runs.
- **Why**: Many enterprise CI environments run in strict, air-gapped, or cost-conscious environments. Aperture must work 100% deterministically and offline by default. When `--explain` is enabled, explanations enrich findings without altering findings count, policy evaluation, or exit codes.
- **Security & Privacy**:
  - Untrusted SQL isolation: User SQL is enclosed within `<untrusted_sql>` XML-style delimiters, paired with strict system directives instructing the model to treat the content solely as passive data and ignore embedded prompt injection attempts.
  - Secret protection: API keys are read strictly from `ANTHROPIC_API_KEY` or `GEMINI_API_KEY`, never printed, logged, or recorded in report artifacts.
  - Cost containment: Explanations are restricted to `ERROR` and `WARNING` severities and capped at a configurable threshold (default: 10).

### 3.7. Schema-Aware Analysis and Version Awareness
- **Decision**: Introduce a `SchemaState` model (`src/aperture/schema.py`) that processes statements across migration files in temporal order (sorted by filename), tracking tables, columns, indexes, and constraints. Provide a configurable `target_postgres_version` (default: 14) in `aperture.yml` so rules adapt to engine-specific capabilities.
- **Why**:
  - *False Positive Reduction*: Tables created earlier in the same migration run (across preceding files or earlier in the same file) are new and empty; operations on them do not hold locks on live data or require zero-downtime workarounds. Rules `MG003`–`MG008` exempt operations on these tables.
  - *Version Awareness*: PostgreSQL's DDL capabilities vary across versions:
    - PostgreSQL 11+ stores constant column defaults in catalog metadata (`pg_attribute.attmissingval`), avoiding a table rewrite on `ADD COLUMN ... NOT NULL DEFAULT <constant>` (ref: [PostgreSQL 11 release notes](https://www.postgresql.org/docs/11/release-11.html)). Volatile defaults (e.g. `clock_timestamp()`) still rewrite the table. In PostgreSQL < 11, adding `NOT NULL DEFAULT` always rewrites the table under `ACCESS EXCLUSIVE` lock.
    - PostgreSQL 12+ optimizes `ALTER COLUMN ... SET NOT NULL` to avoid a full table scan if an equivalent verified `CHECK (col IS NOT NULL)` constraint exists (ref: [PostgreSQL 12 release notes](https://www.postgresql.org/docs/12/release-12.html)).
  - *Relational Dependency Checks*: Rules `MG011` (detecting unindexed foreign keys that cause locking during parent deletes/updates) and `MG012` (detecting drops of columns or tables still referenced by active indexes or foreign keys) require cross-statement relational context.
- **Known Limitations & Boundaries**:
  - *Dynamic SQL & PL/pgSQL*: Aperture parses static SQL ASTs via `pglast`. DDL statements generated inside dynamic SQL string concatenations or `DO $$ ... $$` blocks cannot be inspected statically.
  - *Partial Migration Sets*: When scanning only a subset of migrations (e.g. only files touched in a small PR), `SchemaState` only knows about objects defined or modified within that scanned set. Preexisting production schema objects are not inferred unless defined in the scanned history.

### 3.8. Baseline Mode Architecture
- **Decision**: `aperture baseline --output .aperture-baseline.json` records findings identified by `(rule_id, file, fingerprint)`.
- **Stable Fingerprinting**: Fingerprints are derived by normalizing the SQL statement via `pglast.stream.RawStream` into canonical AST text, stripping leading/trailing whitespace, and hashing with SHA-256. This deliberately excludes line numbers.
- **Why**: If a developer adds a comment, re-indents SQL, or adds a migration statement earlier in the file, line numbers change. AST-based fingerprinting ensures that existing baselined issues remain recognized and excluded from CI failure, while new or modified statements are accurately flagged.

### 3.9. Auto-Fix Engine & Opt-In Boundaries
- **Decision**: `aperture fix <paths> [--write]` automatically rewrites fixable unsafe patterns using PostgreSQL AST node mutations, printing a unified diff by default and modifying files only when `--write` is explicitly passed.
- **Why Opt-In**: Modifying production database migrations can alter transaction semantics and deployment choreography. A dry-run preview guarantees transparency, while `--write` forces conscious developer intent.
- **Deliberate Non-Fixes**:
  - *Destructive operations (`MG001` DROP TABLE / DROP COLUMN)*: Dropping schema objects requires careful application-level deprecation cycles, data backups, and domain context. Automated deletion could lead to catastrophic data loss.
  - *Data rewrites (`MG008` ALTER TYPE, `MG009` UPDATE/DELETE without WHERE)*: Type migrations and table updates require custom dual-writing and batching logic tailored to table volume.
  - *Transaction Block Restrictions (`MG005` inside BEGIN/COMMIT)*: PostgreSQL forbids `CREATE INDEX CONCURRENTLY` inside transaction blocks. Aperture tracks transaction state statement-by-statement: when an un-concurrent index is enclosed between `BEGIN` and `COMMIT`, Aperture refuses to auto-fix and emits an explicit warning.

### 3.10. Dynamic Verification Architecture & Safety Model (`aperture verify`)
- **Decision**: `aperture verify <paths> --dsn <postgres-dsn>` executes migration statements within a transactional session against a live PostgreSQL database, measures real relation locks and table rewrites, compares outcomes against static predictions, and issues a full `ROLLBACK` at completion.
- **Why**: Static analysis detects risky SQL structures based on grammar and version rules, but dynamic verification proves real database behavior against the PostgreSQL engine, verifying whether predicted rewrites or table locks actually manifest.
- **Lock Inspection via `pg_locks`**:
  - Queries `pg_locks` filtered to `pid = pg_backend_pid()` and `locktype = 'relation'`, filtering out system catalogs (`pg_catalog`, `information_schema`, `pg_toast`).
  - Evaluates acquired relation locks against PostgreSQL's table-level lock mode hierarchy (`AccessShareLock` < `RowShareLock` < `RowExclusiveLock` < `ShareUpdateExclusiveLock` < `ShareLock` < `ShareRowExclusiveLock` < `ExclusiveLock` < `AccessExclusiveLock`).
- **Table Rewrite Detection via `pg_class.relfilenode`**:
  - Snapshots `pg_relation_filenode()` for user tables before and after each statement.
  - Physical table rewrites allocate new storage files on disk with a new relfilenode. Comparing pre- and post-execution filenodes confirms whether a table was rewritten.
- **Safety Boundaries**:
  - *Mandatory Confirmation Flag*: Refuses execution without `--i-understand-this-runs-the-migration` to prevent accidental execution against non-throwaway databases.
  - *Credential Masking*: Connection passwords in DSN strings are strictly masked in all reports, logs, and error messages.
  - *Non-Transactional Handling*: Prohibited statements (`CREATE INDEX CONCURRENTLY`, `VACUUM`) are reported as `"not verifiable in a transaction"` and skipped to prevent transaction abort.
- **Operational Limits**:
  - Designed for throwaway test environments (e.g. CI containers or local Docker instances).
  - Empty or small test databases verify lock modes and storage rewrites, but cannot simulate production lock contention queues or duration delays on large tables.


