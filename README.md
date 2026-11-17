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
| `aperture list-rules` | Display a catalog table of all registered rules |
| `aperture explain <RULE_ID>` | Show in-depth explanation, locking risk, and safe alternative |
| `aperture init` | Create a starter `aperture.yml` configuration file |

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

- [ ] **Schema-Aware Analysis with PostgreSQL Version Awareness** *(Planned)*: Track table and column state evolution across historical migrations and customize locking analysis based on target PostgreSQL version behavior.
- [ ] **Baseline Mode and Auto-Fix** *(Planned)*: Snapshot legacy migration directories to baseline existing violations, with automated remediation (`aperture fix`) to generate non-blocking migration code.
- [ ] **Dynamic Verification (`aperture verify`)** *(Planned)*: Spin up ephemeral PostgreSQL containers to execute migrations against synthetic workloads, actively measuring transaction lock acquisition times.
- [ ] **Real-World Benchmark** *(Planned)*: Continuous evaluation against open-source migration suites (GitLab, Mastodon, Discourse) to track detection accuracy and eliminate false positives.

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
