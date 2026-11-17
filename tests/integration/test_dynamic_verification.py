"""Integration tests for dynamic verification against live PostgreSQL.

Requires a running PostgreSQL instance (provided by GitHub Actions CI services
or local docker-compose environment).
"""

import psycopg
import pytest

from aperture.config import Config
from aperture.parser import parse_sql_string
from aperture.verifier import run_dynamic_verification

pytestmark = pytest.mark.integration


# Official PostgreSQL documentation citations:
# - https://www.postgresql.org/docs/current/explicit-locking.html (13.3 Explicit Locking)
# - https://www.postgresql.org/docs/current/view-pg-locks.html (54.12 pg_locks)
# - https://www.postgresql.org/docs/current/catalog-pg-class.html (53.11 pg_class and relfilenode)
# - https://www.postgresql.org/docs/current/sql-altertable.html (ALTER TABLE lock behavior)


def test_dynamic_verification_mg005_confirmed(
    pg_connection_string: str,
    db_conn: psycopg.Connection,
) -> None:
    """MG005: CREATE INDEX without CONCURRENTLY acquires ShareLock (confirmed).

    PostgreSQL documentation:
    https://www.postgresql.org/docs/current/explicit-locking.html
    "CREATE INDEX acquires a SHARE lock on the relation being indexed.
     This blocks concurrent writes (INSERT, UPDATE, DELETE)."
    """
    with db_conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS dyn_test_mg005 CASCADE;")
        cur.execute("CREATE TABLE dyn_test_mg005 (id INT PRIMARY KEY, email TEXT);")

    sql = "CREATE INDEX idx_dyn_mg005 ON dyn_test_mg005 (email);"
    stmts = parse_sql_string(sql)

    report = run_dynamic_verification(
        statements=stmts,
        dsn=pg_connection_string,
        file_path="001_index.sql",
        config=Config(),
    )

    assert report.total_statements == 1
    assert report.statements[0].status == "COMPLETED"
    assert report.statements[0].strongest_locks.get("dyn_test_mg005") == "ShareLock"
    expected_msg = "MG005 on 'dyn_test_mg005' predicted blocking writes: CONFIRMED (ShareLock held)"
    assert any(expected_msg in c for c in report.statements[0].confirmations)
    assert report.total_mismatches == 0


def test_dynamic_verification_mg008_confirmed_and_mismatch(
    pg_connection_string: str,
    db_conn: psycopg.Connection,
) -> None:
    """MG008: ALTER COLUMN TYPE rewrite detection via relfilenode changes.

    PostgreSQL documentation:
    https://www.postgresql.org/docs/current/catalog-pg-class.html
    "relfilenode: Name of the on-disk file of this relation. A table rewrite creates
     a new physical storage file with a new relfilenode."

    Case A (Confirmed): Changing INT to TEXT rewrites the table (relfilenode changes).
    Case B (Mismatch): Changing VARCHAR(50) to VARCHAR(100) is a binary-compatible
    metadata-only operation in PostgreSQL that does NOT rewrite the table.
    """
    with db_conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS dyn_test_mg008 CASCADE;")
        cur.execute(
            "CREATE TABLE dyn_test_mg008 (id INT PRIMARY KEY, score INT, code VARCHAR(50));"
        )

    # Case A: INT -> TEXT rewrites table
    sql_rewrite = "ALTER TABLE dyn_test_mg008 ALTER COLUMN score TYPE TEXT;"
    stmts_rewrite = parse_sql_string(sql_rewrite)

    report_rewrite = run_dynamic_verification(
        statements=stmts_rewrite,
        dsn=pg_connection_string,
        file_path="002_type_rewrite.sql",
        config=Config(),
    )
    assert report_rewrite.statements[0].table_rewrites.get("dyn_test_mg008") is True
    assert any(
        "MG008 on 'dyn_test_mg008' predicted a rewrite: CONFIRMED" in c
        for c in report_rewrite.statements[0].confirmations
    )

    # Case B: VARCHAR(50) -> VARCHAR(100) does NOT rewrite table (mismatch)
    sql_noop = "ALTER TABLE dyn_test_mg008 ALTER COLUMN code TYPE VARCHAR(100);"
    stmts_noop = parse_sql_string(sql_noop)

    report_noop = run_dynamic_verification(
        statements=stmts_noop,
        dsn=pg_connection_string,
        file_path="003_type_noop.sql",
        config=Config(),
    )
    assert report_noop.statements[0].table_rewrites.get("dyn_test_mg008") is False
    assert any(
        "MISMATCH (relfilenode unchanged - metadata only)" in m
        for m in report_noop.statements[0].mismatches
    )
    assert report_noop.total_mismatches == 1


def test_dynamic_verification_mg003_postgres_11_plus_metadata_default(
    pg_connection_string: str,
    db_conn: psycopg.Connection,
) -> None:
    """MG003: ADD COLUMN NOT NULL with constant DEFAULT avoids rewrite on PG 11+.

    PostgreSQL documentation:
    https://www.postgresql.org/docs/11/release-11.html
    "ALTER TABLE ADD COLUMN with a non-null default does not rewrite the table if the
     default value is a constant."
    """
    with db_conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS dyn_test_mg003 CASCADE;")
        cur.execute("CREATE TABLE dyn_test_mg003 (id INT PRIMARY KEY, title TEXT);")
        cur.execute("INSERT INTO dyn_test_mg003 VALUES (1, 'initial');")

    sql = "ALTER TABLE dyn_test_mg003 ADD COLUMN counter INT NOT NULL DEFAULT 42;"
    stmts = parse_sql_string(sql)

    report = run_dynamic_verification(
        statements=stmts,
        dsn=pg_connection_string,
        file_path="004_add_col.sql",
        config=Config(),
    )

    # In PostgreSQL 11+ (CI uses PostgreSQL 16), relfilenode is unchanged
    assert report.statements[0].table_rewrites.get("dyn_test_mg003") is False
    assert any("relfilenode unchanged" in c for c in report.statements[0].confirmations)


def test_fixer_claim_validate_constraint_in_same_transaction_holds_earlier_lock(
    pg_connection_string: str,
    db_conn: psycopg.Connection,
) -> None:
    """Proves the locking claim in fixer.py regarding MG006/MG007.

    PostgreSQL documentation:
    https://www.postgresql.org/docs/current/explicit-locking.html
    "Once acquired, a lock is normally held till end of transaction."

    When ADD CONSTRAINT ... NOT VALID is followed by VALIDATE CONSTRAINT in the
    SAME transaction block (BEGIN...COMMIT), the strong lock (ShareRowExclusiveLock)
    is NOT released before validation. This proves why fixer.py omits VALIDATE
    CONSTRAINT inside transaction blocks.
    """
    with db_conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS test_orders_claim CASCADE;")
        cur.execute("DROP TABLE IF EXISTS test_users_claim CASCADE;")
        cur.execute("CREATE TABLE test_users_claim (id INT PRIMARY KEY);")
        cur.execute("CREATE TABLE test_orders_claim (id INT PRIMARY KEY, user_id INT);")

    # Connect with manual transaction
    with (
        psycopg.connect(pg_connection_string, autocommit=False) as session,
        session.cursor() as cur,
    ):
        cur.execute("SELECT pg_backend_pid();")
        row = cur.fetchone()
        assert row is not None
        pid = row[0]

        # Step 1: Add foreign key with NOT VALID
        cur.execute(
            """
            ALTER TABLE test_orders_claim
            ADD CONSTRAINT fk_claim_user
            FOREIGN KEY (user_id) REFERENCES test_users_claim(id) NOT VALID;
            """
        )

        # Query locks: ShareRowExclusiveLock is held on test_orders_claim
        cur.execute(
            """
            SELECT l.mode
            FROM pg_locks l
            JOIN pg_class c ON l.relation = c.oid
            WHERE l.pid = %s AND c.relname = 'test_orders_claim';
            """,
            (pid,),
        )
        locks_after_add = {row[0] for row in cur.fetchall()}
        assert "ShareRowExclusiveLock" in locks_after_add

        # Step 2: In the SAME transaction, execute VALIDATE CONSTRAINT
        cur.execute("ALTER TABLE test_orders_claim VALIDATE CONSTRAINT fk_claim_user;")

        # Query locks again: ShareRowExclusiveLock is STILL held!
        cur.execute(
            """
            SELECT l.mode
            FROM pg_locks l
            JOIN pg_class c ON l.relation = c.oid
            WHERE l.pid = %s AND c.relname = 'test_orders_claim';
            """,
            (pid,),
        )
        locks_after_validate = {row[0] for row in cur.fetchall()}
        assert "ShareRowExclusiveLock" in locks_after_validate

        # Roll back cleanly
        session.rollback()


def test_dynamic_verification_locks_per_statement_not_cumulative(
    pg_connection_string: str,
    db_conn: psycopg.Connection,
) -> None:
    """Proves that locks are reported per statement and NOT cumulative across the transaction.

    Statement 1 acquires locks on table dyn_test_locks_a.
    Statement 2 acquires locks on table dyn_test_locks_b.
    Even though both statements execute within the same transaction block, Statement 2
    must NOT have dyn_test_locks_a attributed to it in locks_acquired or strongest_locks.
    """
    with db_conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS dyn_test_locks_b CASCADE;")
        cur.execute("DROP TABLE IF EXISTS dyn_test_locks_a CASCADE;")
        cur.execute("CREATE TABLE dyn_test_locks_a (id INT PRIMARY KEY);")
        cur.execute("CREATE TABLE dyn_test_locks_b (id INT PRIMARY KEY);")

    sql = """
    ALTER TABLE dyn_test_locks_a ADD COLUMN col_a INT;
    ALTER TABLE dyn_test_locks_b ADD COLUMN col_b INT;
    """
    stmts = parse_sql_string(sql)

    report = run_dynamic_verification(
        statements=stmts,
        dsn=pg_connection_string,
        file_path="005_multi_table_locks.sql",
        config=Config(),
    )

    assert report.total_statements == 2
    stmt1 = report.statements[0]
    stmt2 = report.statements[1]

    # Statement 1 must report locks on dyn_test_locks_a, NOT dyn_test_locks_b
    assert "dyn_test_locks_a" in stmt1.strongest_locks
    assert "dyn_test_locks_b" not in stmt1.strongest_locks

    # Statement 2 must report locks on dyn_test_locks_b, and MUST NOT attribute
    # dyn_test_locks_a to Statement 2
    assert "dyn_test_locks_b" in stmt2.strongest_locks
    assert "dyn_test_locks_a" not in stmt2.strongest_locks
    assert all(lock.table_name != "dyn_test_locks_a" for lock in stmt2.locks_acquired)
