# 🛡️ Aperture

**Aperture** is an open-source CLI and GitHub Action that scans PostgreSQL migration files in pull requests, detects risky operations (table locks, full scans, table rewrites, data loss), and reports actionable safe alternatives before migrations reach production.

Powered by PostgreSQL's actual parser (`pglast`), Aperture provides zero false positives on PostgreSQL grammar without relying on brittle regular expressions.

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
```

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

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
