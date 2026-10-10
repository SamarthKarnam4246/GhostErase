from __future__ import annotations

from sqlalchemy import Column, String, Text

from backend.app.db.session import Base


class DocumentRecord(Base):
    __tablename__ = "documents"

    id = Column(String, primary_key=True, index=True)
    filename = Column(String, nullable=True)
    source = Column(String, nullable=False)
    content = Column(Text, nullable=True)
    content_hash = Column(String, nullable=False)
    chunk_ids = Column(Text, nullable=False)
    source_file_path = Column(Text, nullable=True)
    status = Column(String, default="stored")
    created_at = Column(String, nullable=True)
    deleted_at = Column(String, nullable=True)
