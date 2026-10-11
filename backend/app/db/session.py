from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy import event, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from backend.app.core.config import settings

SQLALCHEMY_DATABASE_URL = settings.database_url
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False} if SQLALCHEMY_DATABASE_URL.startswith("sqlite") else {})


@event.listens_for(engine, "connect")
def _enforce_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

from backend.app.models.document import DocumentRecord  # noqa: E402,F401
from backend.app.models.deletion import DeletionCheck, DeletionOperation  # noqa: E402,F401
from backend.app.models.lineage import CacheEntry, DerivedArtifact  # noqa: E402,F401


def ensure_schema(target_engine=None) -> None:
    """Create missing tables and apply additive column upgrades.

    Additive only: never drops tables/columns or rewrites user data. Shared
    by application startup and tests that build legacy-shaped databases.
    """
    engine_to_use = target_engine if target_engine is not None else engine
    Base.metadata.create_all(bind=engine_to_use)

    with engine_to_use.begin() as connection:
        document_columns = {column["name"] for column in inspect(connection).get_columns("documents")}
        if "content_hash" not in document_columns:
            connection.execute(text("ALTER TABLE documents ADD COLUMN content_hash VARCHAR NOT NULL DEFAULT ''"))
        if "chunk_ids" not in document_columns:
            connection.execute(text("ALTER TABLE documents ADD COLUMN chunk_ids TEXT NOT NULL DEFAULT '[]'"))
        if "source_file_path" not in document_columns:
            connection.execute(text("ALTER TABLE documents ADD COLUMN source_file_path TEXT"))
        if "created_at" not in document_columns:
            connection.execute(text("ALTER TABLE documents ADD COLUMN created_at VARCHAR"))
        operation_columns = {column["name"] for column in inspect(connection).get_columns("deletion_operations")}
        if "kind" not in operation_columns:
            connection.execute(
                text("ALTER TABLE deletion_operations ADD COLUMN kind VARCHAR NOT NULL DEFAULT 'deletion'")
            )
        if "scope_snapshot" not in operation_columns:
            connection.execute(text("ALTER TABLE deletion_operations ADD COLUMN scope_snapshot TEXT"))
        if "result_snapshot" not in operation_columns:
            connection.execute(text("ALTER TABLE deletion_operations ADD COLUMN result_snapshot TEXT"))


ensure_schema()
