from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy import inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from backend.app.core.config import settings

SQLALCHEMY_DATABASE_URL = settings.database_url
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False} if SQLALCHEMY_DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

from backend.app.models.document import DocumentRecord  # noqa: E402,F401
from backend.app.models.deletion import DeletionCheck, DeletionOperation  # noqa: E402,F401

Base.metadata.create_all(bind=engine)

with engine.begin() as connection:
    document_columns = {column["name"] for column in inspect(connection).get_columns("documents")}
    if "content_hash" not in document_columns:
        connection.execute(text("ALTER TABLE documents ADD COLUMN content_hash VARCHAR NOT NULL DEFAULT ''"))
    if "chunk_ids" not in document_columns:
        connection.execute(text("ALTER TABLE documents ADD COLUMN chunk_ids TEXT NOT NULL DEFAULT '[]'"))
    if "source_file_path" not in document_columns:
        connection.execute(text("ALTER TABLE documents ADD COLUMN source_file_path TEXT"))
    if "created_at" not in document_columns:
        connection.execute(text("ALTER TABLE documents ADD COLUMN created_at VARCHAR"))
