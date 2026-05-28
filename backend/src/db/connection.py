"""Async PostgreSQL connection pool using environment variables."""

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

import structlog

logger = structlog.get_logger(__name__)

# Database configuration from environment variables
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "stocks")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_MIN_CONNECTIONS = int(os.environ.get("DB_MIN_CONNECTIONS", "1"))
DB_MAX_CONNECTIONS = int(os.environ.get("DB_MAX_CONNECTIONS", "10"))


class DatabasePool:
    """Manages a PostgreSQL connection pool."""

    _pool: pool.ThreadedConnectionPool | None = None

    @classmethod
    def get_dsn(cls) -> str:
        """Build the database connection string from environment variables."""
        return (
            f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} "
            f"user={DB_USER} password={DB_PASSWORD}"
        )

    @classmethod
    def initialize(cls) -> None:
        """Initialize the connection pool."""
        if cls._pool is not None:
            return

        try:
            cls._pool = pool.ThreadedConnectionPool(
                minconn=DB_MIN_CONNECTIONS,
                maxconn=DB_MAX_CONNECTIONS,
                dsn=cls.get_dsn(),
            )
            logger.info(
                "Database connection pool initialized",
                host=DB_HOST,
                port=DB_PORT,
                database=DB_NAME,
                min_connections=DB_MIN_CONNECTIONS,
                max_connections=DB_MAX_CONNECTIONS,
            )
        except psycopg2.Error as e:
            logger.error("Failed to initialize database pool", error=str(e))
            raise

    @classmethod
    def close(cls) -> None:
        """Close all connections in the pool."""
        if cls._pool is not None:
            cls._pool.closeall()
            cls._pool = None
            logger.info("Database connection pool closed")

    @classmethod
    @asynccontextmanager
    async def get_connection() -> AsyncGenerator:
        """Get a connection from the pool as an async context manager.

        Usage:
            async with DatabasePool.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT * FROM stocks")
                    rows = cur.fetchall()
        """
        if DatabasePool._pool is None:
            DatabasePool.initialize()

        conn = DatabasePool._pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            DatabasePool._pool.putconn(conn)


@asynccontextmanager
async def get_db_connection() -> AsyncGenerator:
    """Convenience function to get a database connection.

    Usage:
        async with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT 1")
    """
    async with DatabasePool.get_connection() as conn:
        yield conn


async def run_migrations(migrations_dir: str | None = None) -> None:
    """Run SQL migration files in order.

    Args:
        migrations_dir: Path to the migrations directory.
                       Defaults to the migrations/ directory next to this file.
    """
    if migrations_dir is None:
        migrations_dir = os.path.join(os.path.dirname(__file__), "migrations")

    migration_files = sorted(
        f for f in os.listdir(migrations_dir) if f.endswith(".sql")
    )

    async with get_db_connection() as conn:
        with conn.cursor() as cur:
            for migration_file in migration_files:
                filepath = os.path.join(migrations_dir, migration_file)
                logger.info("Running migration", file=migration_file)

                with open(filepath, "r") as f:
                    sql = f.read()

                cur.execute(sql)

            logger.info(
                "All migrations completed",
                count=len(migration_files),
            )
