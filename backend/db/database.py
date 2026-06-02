import os
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

# Use Electron userData dir in production, fallback to backend/data/ in dev
_DATA_DIR = os.environ.get("DENTAL_AGENT_DATA_DIR")
DATABASE_DIR = os.path.join(_DATA_DIR, "data") if _DATA_DIR else os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DATABASE_PATH = os.path.join(DATABASE_DIR, "app.db")
DATABASE_URL = f"sqlite+aiosqlite:///{DATABASE_PATH}"

engine = create_async_engine(DATABASE_URL, echo=False)


# Enable WAL mode for better concurrent read/write performance (§10.1)
@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with async_session() as session:
        yield session


async def init_db():
    os.makedirs(DATABASE_DIR, exist_ok=True)
    async with engine.begin() as conn:
        from db.models import Base
        await conn.run_sync(Base.metadata.create_all)

    # Run incremental migrations for existing databases
    await _run_migrations()


async def _run_migrations():
    """Add missing columns/tables to existing databases.

    SQLAlchemy create_all() only creates new tables; it does NOT add
    columns to existing tables. This function checks for missing columns
    and adds them via ALTER TABLE.
    """
    import logging
    logger = logging.getLogger(__name__)

    # table -> [(column_def_sql, column_name)]
    MIGRATIONS = {
        "user_purchases": [
            ("import_batch VARCHAR(64) NOT NULL DEFAULT ''", "import_batch"),
        ],
        "llm_config": [
            ("langsmith_api_key VARCHAR(200) NOT NULL DEFAULT ''", "langsmith_api_key"),
            ("langsmith_project VARCHAR(100) NOT NULL DEFAULT 'dental-recommend-agent'", "langsmith_project"),
            ("langsmith_enabled INTEGER NOT NULL DEFAULT 0", "langsmith_enabled"),
            ("available_models TEXT NOT NULL DEFAULT '[]'", "available_models"),
            ("models_updated_at DATETIME", "models_updated_at"),
            ("connection_status VARCHAR(20) NOT NULL DEFAULT 'untested'", "connection_status"),
            ("last_test_at DATETIME", "last_test_at"),
        ],
    }

    async with engine.begin() as conn:
        for table, columns in MIGRATIONS.items():
            # Check if table exists
            result = await conn.run_sync(
                lambda sync_conn: sync_conn.execute(
                    __import__("sqlalchemy").text(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name=:t"
                    ),
                    {"t": table},
                ).fetchone()
            )
            if result is None:
                continue  # table doesn't exist yet, create_all will handle it

            # Get existing columns
            existing = await conn.run_sync(
                lambda sync_conn: [
                    row[1] for row in sync_conn.execute(
                        __import__("sqlalchemy").text(f"PRAGMA table_info({table})")
                    ).fetchall()
                ]
            )

            for col_def, col_name in columns:
                if col_name not in existing:
                    sql = f"ALTER TABLE {table} ADD COLUMN {col_def}"
                    await conn.execute(__import__("sqlalchemy").text(sql))
                    logger.info("Migration: added %s.%s", table, col_name)

    logger.info("Database migrations complete.")
