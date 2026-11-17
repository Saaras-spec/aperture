# 🛡️ Aperture

[![CI](https://github.com/Saaras-spec/aperture/actions/workflows/ci.yml/badge.svg)](https://github.com/Saaras-spec/aperture/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-95%25-brightgreen)](https://github.com/Saaras-spec/aperture)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Aperture** is an open-source CLI and GitHub Action that scans PostgreSQL migration files in pull requests, detects risky operations (table locks, full scans, table rewrites, data loss), and reports actionable safe alternatives before migrations reach production.

Powered by PostgreSQL's actual parser (`pglast`), Aperture provides zero false positives on PostgreSQL grammar without relying on brittle regular expressions.

```text
$ aperture check migrations/002_unsafe.sql

migrations/002_unsafe.sql
  line 4: [ERROR] MG005: Creating index 'idx_users_email' on table 'users' without CONCURRENTLY blocks all write operations until index creation completes.
     SQL: CREATE INDEX idx_users_email ON users(email)
     💡 Add the CONCURRENTLY keyword:
     💡 CREATE INDEX CONCURRENTLY idx_users_email ON ...;
     💡 Note: CONCURRENTLY cannot run inside a transaction block.

  line 7: [ERROR] MG001: Dropping column 'phone' from table 'users' causes irreversible data loss.
     SQL: ALTER TABLE users DROP COLUMN phone
     💡 Remove all references to 'phone' in application code first. Deploy and monitor, then drop the column in a subsequent release.

Summary: Found 2 error(s), 0 warning(s) across 1 file(s).
Result: FAILED (violations at or above ERROR)
```

---

## 🚀 Try the Demo

A complete sample project is provided in [`demo/`](demo/):

```bash
# 1. Scan safe migration (passes with 0 findings, exit code 0)
aperture check demo/db/migrations/001_safe.sql

# 2. Scan unsafe migration (blocks dangerous DROP COLUMN and non-concurrent index, exit code 1)
aperture check demo/db/migrations/002_unsafe.sql

# 3. View the rich Markdown PR comment report Aperture produces
aperture check demo/db/migrations/002_unsafe.sql --format markdown
```

Check out [`demo/README.md`](demo/README.md) to learn how to set up Aperture in your repository and simulate blocked pull requests.

---

## ⚡ Quick Start

### 1. Installation

```bash
pip install aperture
```

Or run via Docker:

```bash
docker run --rm -v $(pwd):/app/workspace ghcr.io/saaras-spec/aperture:latest check
```

### 2. Initialize Configuration

```bash
aperture init
```

This generates a starter `aperture.yml` in your repository root.

### 3. Run Checks

```bash
# Scan all migration files matching configured globs
aperture check

# Scan specific migration files or directories
aperture check migrations/001_create_users.sql

# Scan only migrations changed in a pull request against main
aperture check --base-ref origin/main

# Output in different formats (console, markdown, json, sarif)
aperture check --format markdown --output pr_comment.md
aperture check --format sarif --output results.sarif

# Filter known legacy findings using a baseline snapshot
aperture check --baseline .aperture-baseline.json

# Preview automated safe rewrites as a unified diff
aperture fix migrations/

# Apply safe rewrites in place
aperture fix migrations/ --write

# Enable optional AI-powered plain-English explanations and migration plans
aperture check --explain
```

---

## 🤖 Optional AI Risk Explainer (`--explain`)

Aperture can optionally enrich detected findings with AI-generated plain-English explanations and custom step-by-step migration plans:

```bash
# Enable AI explanations using Anthropic Claude
export ANTHROPIC_API_KEY="sk-ant-..."
aperture check --explain

# Or using Google Gemini
export GEMINI_API_KEY="AIzaSy..."
aperture check --explain
```

> [!WARNING]
> **Data Privacy Notice:** When `--explain` is enabled, the SQL statements and diagnostic messages of flagged `ERROR` and `WARNING` findings are sent to the configured provider API (Anthropic Claude or Google Gemini). Do not enable this flag if your organization prohibits sending schema definitions to external LLM providers.

### Explainer Highlights & Safety Guardrails
- **Zero Lock-In**: Works completely offline by default. If no API key is set, the flag is off, or an API call fails or times out, Aperture silently falls back to built-in rule suggestions.
- **Invariant Exit Codes**: The AI explainer never alters which findings exist, the pass/fail determination, or process exit codes.
- **Untrusted Input Isolation**: SQL text is wrapped in strict `<untrusted_sql>` delimiters, and the model is instructed to treat all contents solely as passive data and ignore embedded prompt injection attempts.
- **Cost Controls**: Explanations are restricted to `ERROR` and `WARNING` severities and capped at a configurable limit (default: 10 per run).
- **Default & Configurable Models**:
  - Anthropic default: `claude-haiku-5-5`
  - Google Gemini default: `gemini-2.5-flash` (Google's official recommended Flash model)
  - Both models can be customized in `aperture.yml`.
- **Custom Configuration (`aperture.yml`)**:
  ```yaml
  explain_provider: anthropic   # 'anthropic' or 'gemini' (default: auto-detect)
  explain_model: claude-haiku-5-5 # or 'gemini-2.5-flash'
  explain_max_findings: 10      # Maximum findings to explain per run
  explain_timeout_seconds: 10.0 # HTTP request timeout
  ```

### 🧪 Verifying `--explain`

Follow these exact steps to verify the AI explainer with a real key:

1. **Set your API key**:
   ```bash
   # For Anthropic Claude
   export ANTHROPIC_API_KEY="sk-ant-..."

   # Or for Google Gemini
   export GEMINI_API_KEY="AIzaSy..."
   ```

2. **Run `aperture check` with `--explain`**:
   ```bash
   aperture check demo/db/migrations/002_unsafe.sql --explain
   ```

3. **Observe the enriched output**:
   - Aperture flags `MG001` (`DROP COLUMN`) and `MG005` (`CREATE INDEX` without `CONCURRENTLY`).
   - Alongside the built-in recommendation, each finding displays a dedicated `🤖 AI-generated explanation:` section with a plain-English explanation and a step-by-step safe migration plan.
   - The exit code remains `1` (reflecting detected policy violations).

4. **Test silent fallback & stderr diagnostic warning**:
   Unset your API key and run again:
   ```bash
   unset ANTHROPIC_API_KEY GEMINI_API_KEY
   aperture check demo/db/migrations/002_unsafe.sql --explain
   ```
   Aperture prints one short diagnostic line to `stderr`:
   ```text
   aperture: AI explanation unavailable (no ANTHROPIC_API_KEY or GEMINI_API_KEY set)
   ```
   The findings and built-in suggestions are displayed normally to `stdout`, and the exit code remains `1`.

---

## 📋 Available Commands

| Command | Description |
| :--- | :--- |
| `aperture check [PATHS...]` | Scan migration files for dangerous operations |
| `aperture baseline [PATHS...]` | Snapshot current findings into `.aperture-baseline.json` |
| `aperture fix [PATHS...] [--write]` | Preview or apply automated AST safe migration rewrites |
| `aperture list-rules` | Display a catalog table of all registered rules |
| `aperture explain <RULE_ID>` | Show in-depth explanation, locking risk, and safe alternative |
| `aperture init` | Create a starter `aperture.yml` configuration file |

---

## 📌 Baseline Mode

When adopting Aperture on existing legacy codebases with hundreds of historical migrations, you may not want to immediately fail CI on pre-existing legacy statements. **Baseline mode** lets you record current findings as an accepted baseline, ensuring PR checks only fail on newly introduced risky operations.

### 1. Create a Baseline Snapshot

```bash
# Record all current migration findings into a baseline file
aperture baseline --output .aperture-baseline.json
```

Aperture creates stable fingerprints from the canonical AST of each statement (ignoring whitespace, indentation, comments, and line number shifts). If lines move or non-semantic formatting changes occur, fingerprints remain unchanged.

### 2. Check Against the Baseline

```bash
# Run CI check filtering out baselined legacy findings
aperture check --baseline .aperture-baseline.json
```

- Baselined findings are excluded from the failure count and detail outputs.
- The summary indicates how many findings were excluded: `(3 baseline finding(s) excluded)`.
- CI exits non-zero **only** if new, un-baselined violations at or above `fail_on` are introduced.

### Duplicate Statement Handling (Multiset Accounting)

Aperture tracks baselined findings using occurrence-aware frequency counts (`(rule_id, file, fingerprint)`):
- If a migration file contains one instance of an unsafe statement recorded in the baseline, and a developer adds a second identical statement to the same file, the baseline covers only the single original instance.
- The newly added duplicate is detected as a new violation and causes checks to fail, ensuring that existing baseline entries cannot silently hide newly introduced duplicate risks.

---

## 🛠️ Automated Safe Rewrites (`aperture fix`)

Aperture can automatically rewrite risky statements using non-blocking, zero-downtime PostgreSQL migration patterns. Rewrites are performed using PostgreSQL AST node mutations (`pglast`), never fragile regex string replacements.

### 1. Preview Rewrites (Dry Run by Default)

```bash
aperture fix migrations/
```

By default, `aperture fix` is completely non-destructive: it outputs a standard unified diff of proposed changes to `stdout` without touching any files on disk.

### 2. Apply Rewrites in Place

```bash
aperture fix migrations/ --write
```

Rewrites the migration files in place with safe alternatives.

### Supported Auto-Fix Rules

| Rule | Unsafe Pattern | Safe AST Rewrite Applied |
| :--- | :--- | :--- |
| **MG005** | `CREATE INDEX idx ON t(col);` | `CREATE INDEX CONCURRENTLY idx ON t (col);` |
| **MG006** | `ALTER TABLE t ADD CONSTRAINT fk FOREIGN KEY (c) REFERENCES r(id);` | `ALTER TABLE t ADD CONSTRAINT fk FOREIGN KEY (c) REFERENCES r (id) NOT VALID;`<br>`ALTER TABLE t VALIDATE CONSTRAINT fk;` |
| **MG007** | `ALTER TABLE t ADD CONSTRAINT chk CHECK (c > 0);` | `ALTER TABLE t ADD CONSTRAINT chk CHECK (c > 0) NOT VALID;`<br>`ALTER TABLE t VALIDATE CONSTRAINT chk;` |
| **MG010** | `ALTER TABLE t ADD COLUMN c INT;` *(missing lock timeout)* | Inserts `SET lock_timeout = '2s';` at the top of the migration file. |

### Limitations & Transaction Blocks

- **Transaction Block Restrictions**: In PostgreSQL, `CREATE INDEX CONCURRENTLY` cannot run inside an explicit transaction block (`BEGIN` ... `COMMIT`). If Aperture detects that a `CREATE INDEX` statement is wrapped inside a transaction block, it skips the rewrite and outputs an explicit warning to `stderr` explaining that the index creation must be moved outside the transaction.
- **Opt-In Safety**: Rewriting SQL migrations can impact deployment choreography. Auto-fix is strictly opt-in and requires the `--write` flag to modify files on disk. Destructive operations (such as `DROP TABLE` or `DROP COLUMN`) require manual engineering decisions and are intentionally never auto-fixed.

---

## 🤖 GitHub Action Usage

Integrate Aperture directly into your GitHub Actions CI pipeline:

```yaml
name: Migration Safety Guard

on:
  pull_request:
    paths:
      - "migrations/**"

jobs:
  check-migrations:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write # Required for posting PR comments
      security-events: write # Required if uploading SARIF

    steps:
      - name: Checkout PR branch
        uses: actions/checkout@v4
        with:
          fetch-depth: 0 # Full history for git diff

      - name: Run Aperture
        uses: Saaras-spec/aperture@main
        with:
          base-ref: "origin/${{ github.base_ref }}"
          fail-on: "ERROR"
          github-token: ${{ secrets.GITHUB_TOKEN }}
          comment-on-pr: "true"
```

---

## 🔍 Rules Catalog

| Rule ID | Default Severity | Name | Description |
| :--- | :--- | :--- | :--- |
| **MG001** | `ERROR` | DROP TABLE or DROP COLUMN | Irreversible data loss and access exclusive locks |
| **MG002** | `WARNING` | Rename column or table | Breaks running app versions during rolling deployments |
| **MG003** | `ERROR` | ADD COLUMN NOT NULL without DEFAULT | Fails on non-empty tables and holds table lock |
| **MG004** | `WARNING` | ALTER COLUMN SET NOT NULL | Full sequential table scan under access exclusive lock |
| **MG005** | `ERROR` | CREATE INDEX without CONCURRENTLY | Blocks all concurrent writes for duration of build |
| **MG006** | `ERROR` | ADD FOREIGN KEY without NOT VALID | Full table scan locking both tables |
| **MG007** | `WARNING` | ADD CHECK constraint without NOT VALID | Full table scan under access exclusive lock |
| **MG008** | `ERROR` | ALTER COLUMN TYPE | Full table rewrite and index rebuild under exclusive lock |
| **MG009** | `ERROR` | UPDATE or DELETE without WHERE | Mass row modification or accidental table wipeout |
| **MG010** | `WARNING` | Risky ALTER TABLE without lock_timeout | Stalls in lock queue and blocks incoming application traffic |
| **MG011** | `WARNING` | ADD FOREIGN KEY without index on referencing column | Unindexed foreign key causes sequential scans or locks during referenced deletes/updates |
| **MG012** | `ERROR` | DROP COLUMN or DROP TABLE with active dependents | Relation or column referenced by foreign key (ERROR) or index (WARNING) |

---

## 🤫 Inline Suppressions

To suppress a rule for an approved, DBA-reviewed statement, place an ignore comment on the line preceding the statement. **A non-empty justification reason is mandatory:**

```sql
-- aperture:ignore MG001 Dropping legacy table after verifying zero queries in v2.4
DROP TABLE legacy_users;
```

If an ignore directive omits the reason (e.g. `-- aperture:ignore MG001`), Aperture flags an `MG000` warning and refuses to suppress the rule.

---

## ⚙️ Configuration (`aperture.yml`)

```yaml
fail_on: ERROR
target_postgres_version: 14 # default: 14 (supports 9 to 18)

migration_paths:
  - "migrations/**/*.sql"
  - "db/migrate/*.sql"

large_tables:
  - users
  - orders
  - audit_logs

rules:
  MG001:
    enabled: true
    severity: ERROR
  MG002:
    enabled: true
    severity: WARNING
```

---

## 🗺️ Roadmap

- [x] **Schema-Aware Analysis with PostgreSQL Version Awareness**: Track table and column state evolution across historical migrations and customize locking analysis based on target PostgreSQL version behavior.
- [x] **Baseline Mode and Auto-Fix**: Snapshot legacy migration directories to baseline existing violations, with automated remediation (`aperture fix`) to generate non-blocking migration code.
- [ ] **Dynamic Verification (`aperture verify`)** *(Planned)*: Apply migrations on a throwaway PostgreSQL database and record the locks each statement acquires and whether tables are rewritten, to check the static rules against real behaviour.
- [ ] **Real-World Benchmark** *(Planned)*: Run Aperture on open-source projects that use plain SQL migration files and report findings, false-positive rate and speed.

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
