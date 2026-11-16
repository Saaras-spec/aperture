# Aperture Demo Repository

This folder demonstrates how **Aperture** safeguards your database migrations in a production CI/CD workflow.

---

## What's in this Demo?

- `db/migrations/001_safe.sql`: Initial safe migration using `CONCURRENTLY`, `SET lock_timeout`, and non-blocking `ADD COLUMN ... DEFAULT`.
- `db/migrations/002_unsafe.sql`: Risky migration containing:
  - `MG005`: `CREATE INDEX` without `CONCURRENTLY` (takes a `ShareLock` blocking concurrent writes).
  - `MG001`: `DROP COLUMN` (causes irreversible data loss and breaks zero-downtime rolling deploys).
- `aperture.yml`: Project configuration setting `fail_on: ERROR` and targeting `db/migrations/*.sql`.
- `.github/workflows/aperture.yml`: GitHub Action configuration that runs Aperture on every PR and automatically posts a markdown diagnostic comment with safe alternatives.

---

## How to Test Locally

You can run Aperture directly from the project root against the demo migrations:

```bash
# 1. Scan the safe migration (passes with 0 findings, exit code 0)
aperture check demo/db/migrations/001_safe.sql

# 2. Scan the unsafe migration (detects risks, exit code 1)
aperture check demo/db/migrations/002_unsafe.sql

# 3. View the formatted markdown report Aperture generates for PRs
aperture check demo/db/migrations/002_unsafe.sql --format markdown
```

---

## How to Open a PR That Gets Blocked

To see Aperture block a pull request and post a rich comment in GitHub:

1. **Create a new branch**:
   ```bash
   git checkout -b feature/unsafe-migration
   ```

2. **Commit `demo/db/migrations/002_unsafe.sql`**:
   ```bash
   git add demo/db/migrations/002_unsafe.sql
   git commit -m "feat: add index and drop unused email column"
   ```

3. **Push to GitHub and open a Pull Request against `main`**:
   ```bash
   git push origin feature/unsafe-migration
   ```

4. **Observe the Results**:
   - The GitHub Action runs `aperture check --base-ref origin/main`.
   - Aperture detects the changes in `db/migrations/002_unsafe.sql`.
   - The workflow exits with code `1`, blocking the PR from merging.
   - Aperture posts a detailed comment on the PR detailing `MG001` and `MG005` along with copy-pasteable safe migration patterns!
