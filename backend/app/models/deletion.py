from __future__ import annotations

from sqlalchemy import Column, ForeignKey, String, Text

from backend.app.db.session import Base


class DeletionOperation(Base):
    __tablename__ = "deletion_operations"

    id = Column(String, primary_key=True, index=True)
    document_id = Column(String, ForeignKey("documents.id"), nullable=False, index=True)
    chunk_ids = Column(Text, nullable=False)
    status = Column(String, nullable=False)
    created_at = Column(String, nullable=False)
    completed_at = Column(String, nullable=True)


class DeletionCheck(Base):
    __tablename__ = "deletion_checks"

    id = Column(String, primary_key=True, index=True)
    operation_id = Column(String, ForeignKey("deletion_operations.id"), nullable=False, index=True)
    document_id = Column(String, ForeignKey("documents.id"), nullable=False, index=True)
    chunk_ids = Column(Text, nullable=False)
    layer = Column(String, nullable=False)
    method = Column(String, nullable=False)
    status = Column(String, nullable=False)
    actual_result = Column(Text, nullable=False)
    error_detail = Column(Text, nullable=True)
    checked_at = Column(String, nullable=False)
