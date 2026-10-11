from __future__ import annotations

from sqlalchemy import Column, ForeignKey, String, Text

from backend.app.db.session import Base


class DerivedArtifact(Base):
    """Current-state lineage pointer, not evidence.

    Deletion evidence lives in append-only ``deletion_checks`` rows; this table
    records what derived artifacts are known for a document and their latest
    lifecycle state so deletion can find them. Identifiers and hashes only --
    never raw document text.
    """

    __tablename__ = "derived_artifacts"

    id = Column(String, primary_key=True, index=True)
    document_id = Column(String, ForeignKey("documents.id"), nullable=False, index=True)
    artifact_type = Column(String, nullable=False)
    backend = Column(String, nullable=False)
    identifier = Column(String, nullable=False)
    parent_id = Column(String, ForeignKey("derived_artifacts.id"), nullable=True)
    status = Column(String, nullable=False, default="active")
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)


class CacheEntry(Base):
    """Application-owned demo cache row, not a third-party cache claim.

    One row per (query hash, document): the cached retrieval result is stored
    as chunk/document IDs plus distances. Raw query text and document bodies
    are never stored -- only the SHA-256 query hash and result IDs.
    """

    __tablename__ = "cache_entries"

    cache_key = Column(String, primary_key=True, index=True)
    document_id = Column(String, ForeignKey("documents.id"), nullable=False, index=True)
    query_hash = Column(String, nullable=False, index=True)
    result_ids = Column(Text, nullable=False)
    created_at = Column(String, nullable=False)
    invalidated_at = Column(String, nullable=True)
