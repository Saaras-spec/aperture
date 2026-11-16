"""Integration test fixtures for live PostgreSQL testing."""

import os
from collections.abc import Generator

import psycopg
import pytest

# Default connection URL matching docker-compose.yml
DEFAULT_PG_URL = "postgresql://aperture_user:aperture_password@localhost:5432/aperture_test"


def get_pg_url() -> str:
    """Return configured PostgreSQL URL."""
    return os.environ.get("POSTGRES_TEST_URL", DEFAULT_PG_URL)


@pytest.fixture(scope="session")
def pg_connection_string() -> str:
    """Return database connection string or skip if PostgreSQL is unavailable."""
    url = get_pg_url()
    try:
        with psycopg.connect(url, connect_timeout=3) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1;")
    except Exception as exc:
        pytest.skip(f"Live PostgreSQL instance unavailable at {url}: {exc}")
    return url


@pytest.fixture
def db_conn(pg_connection_string: str) -> Generator[psycopg.Connection, None, None]:
    """Provide an isolated PostgreSQL connection with autocommit enabled."""
    with psycopg.connect(pg_connection_string, autocommit=True) as conn:
        yield conn
