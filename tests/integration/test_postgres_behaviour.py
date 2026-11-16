"""Integration tests validating real PostgreSQL behaviour against Aperture rules.

These tests run against a real PostgreSQL instance (via Docker or CI service container)
and prove that the risks flagged by Aperture rules (MG003, MG004, MG005, MG006)
correspond to actual database locks and operational failures.
"""

import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.integration


def test_mg003_add_column_not_null_without_default_fails_on_non_empty_table(
    db_conn: psycopg.Connection,
) -> None:
    """MG003 proof: ADD COLUMN NOT NULL without DEFAULT fails on non-empty table."""
    with db_conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS test_mg003_items CASCADE;")
        cur.execute("CREATE TABLE test_mg003_items (id SERIAL PRIMARY KEY, name TEXT);")
        cur.execute("INSERT INTO test_mg003_items (name) VALUES ('existing_item');")

        # 1. Dangerous migration (MG003 violation): Adding NOT NULL column without DEFAULT fails
        with pytest.raises(errors.NotNullViolation) as exc_info:
            cur.execute("ALTER TABLE test_mg003_items ADD COLUMN price NUMERIC NOT NULL;")
        assert "contains null values" in str(exc_info.value).lower()

        # 2. Safe alternative: Adding NOT NULL column WITH a DEFAULT succeeds
        cur.execute("ALTER TABLE test_mg003_items ADD COLUMN price NUMERIC NOT NULL DEFAULT 0.0;")

        # Verify column and data exist
        cur.execute("SELECT id, name, price FROM test_mg003_items;")
        row = cur.fetchone()
        assert row is not None
        assert row[1] == "existing_item"
        assert row[2] == 0.0


def test_mg005_share_lock_blocks_inserts_while_share_update_exclusive_allows(
    pg_connection_string: str,
    db_conn: psycopg.Connection,
) -> None:
    """MG005 proof: CREATE INDEX (ShareLock) blocks writes, whereas CONCURRENTLY allows writes.

    CREATE INDEX takes a SHARE lock on the relation.
    CREATE INDEX CONCURRENTLY takes a SHARE UPDATE EXCLUSIVE lock.
    INSERT statements require a ROW EXCLUSIVE lock.
    - ROW EXCLUSIVE conflicts with SHARE (standard index blocks inserts).
    - ROW EXCLUSIVE does NOT conflict with SHARE UPDATE EXCLUSIVE
      (concurrent index permits concurrent inserts).
    """
    with db_conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS test_mg005_events CASCADE;")
        cur.execute("CREATE TABLE test_mg005_events (id SERIAL PRIMARY KEY, payload TEXT);")

    # Connect two concurrent sessions
    with (
        psycopg.connect(pg_connection_string) as session1,
        psycopg.connect(pg_connection_string, autocommit=True) as session2,
    ):
        # Case A: Standard CREATE INDEX lock level (SHARE MODE)
        with session1.cursor() as cur1, session2.cursor() as cur2:
            session1.execute("BEGIN;")
            cur1.execute("LOCK TABLE test_mg005_events IN SHARE MODE;")

            # Session 2 tries an INSERT with a short lock_timeout
            cur2.execute("SET lock_timeout = '300ms';")
            with pytest.raises(errors.LockNotAvailable):
                cur2.execute("INSERT INTO test_mg005_events (payload) VALUES ('blocked_event');")

            session1.rollback()

        # Case B: CREATE INDEX CONCURRENTLY lock level (SHARE UPDATE EXCLUSIVE MODE)
        with session1.cursor() as cur1, session2.cursor() as cur2:
            session1.execute("BEGIN;")
            cur1.execute("LOCK TABLE test_mg005_events IN SHARE UPDATE EXCLUSIVE MODE;")

            # Session 2 attempts concurrent INSERT: succeeds because locks do not conflict
            cur2.execute("SET lock_timeout = '300ms';")
            cur2.execute("INSERT INTO test_mg005_events (payload) VALUES ('allowed_event');")

            session1.rollback()

            cur2.execute("SELECT payload FROM test_mg005_events;")
            rows = cur2.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "allowed_event"


def test_mg004_set_not_null_fails_on_existing_null_values(
    db_conn: psycopg.Connection,
) -> None:
    """MG004 proof: SET NOT NULL on existing column with NULLs fails with NotNullViolation."""
    with db_conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS test_mg004_users CASCADE;")
        cur.execute("CREATE TABLE test_mg004_users (id SERIAL PRIMARY KEY, nickname TEXT);")
        cur.execute("INSERT INTO test_mg004_users (nickname) VALUES (NULL);")

        # 1. Setting NOT NULL directly on column containing NULL fails
        with pytest.raises(errors.NotNullViolation):
            cur.execute("ALTER TABLE test_mg004_users ALTER COLUMN nickname SET NOT NULL;")

        # 2. Safe alternative: Backfill existing NULLs first, then SET NOT NULL
        cur.execute("UPDATE test_mg004_users SET nickname = 'guest' WHERE nickname IS NULL;")
        cur.execute("ALTER TABLE test_mg004_users ALTER COLUMN nickname SET NOT NULL;")

        # Verify
        cur.execute("SELECT nickname FROM test_mg004_users;")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "guest"


def test_mg006_foreign_key_not_valid_allows_deferred_validation(
    db_conn: psycopg.Connection,
) -> None:
    """MG006 proof: ADD FOREIGN KEY without NOT VALID blocks if invalid rows exist;

    NOT VALID allows adding the constraint without full historical scan.
    """
    with db_conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS test_mg006_child CASCADE;")
        cur.execute("DROP TABLE IF EXISTS test_mg006_parent CASCADE;")
        cur.execute("CREATE TABLE test_mg006_parent (id INT PRIMARY KEY);")
        cur.execute("CREATE TABLE test_mg006_child (id INT PRIMARY KEY, parent_id INT);")

        # Insert an orphan record in child
        cur.execute("INSERT INTO test_mg006_child (id, parent_id) VALUES (1, 999);")

        # 1. Standard ADD CONSTRAINT without NOT VALID fails immediately
        with pytest.raises(errors.ForeignKeyViolation):
            cur.execute(
                "ALTER TABLE test_mg006_child "
                "ADD CONSTRAINT fk_child_parent "
                "FOREIGN KEY (parent_id) REFERENCES test_mg006_parent(id);"
            )

        # 2. Adding with NOT VALID succeeds (skips table scan / historical verification)
        cur.execute(
            "ALTER TABLE test_mg006_child "
            "ADD CONSTRAINT fk_child_parent "
            "FOREIGN KEY (parent_id) REFERENCES test_mg006_parent(id) NOT VALID;"
        )
