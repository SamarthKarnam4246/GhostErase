from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from backend.app.models.lineage import CacheEntry, DerivedArtifact


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_reference(entry: Any) -> bool:
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("chunk_id"), str)
        and isinstance(entry.get("distance"), (int, float))
    )


def count_results(result_ids: str) -> int:
    """Count cached chunk references, tolerating malformed rows."""
    try:
        entries = json.loads(result_ids)
    except (ValueError, TypeError):
        return 0
    if not isinstance(entries, list):
        return 0
    return sum(1 for entry in entries if _valid_reference(entry))


def query_hash_for(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def cache_key_for(query_hash: str, document_id: str) -> str:
    return f"{query_hash}:{document_id}"


class AppCache:
    """Persistent application-owned demo cache backed by SQLite.

    This is GhostErase's own retrieval cache for the stale-cache demo. It is
    not a claim that arbitrary third-party caches can be inspected. Cached
    values are chunk/document IDs plus distances -- never query text or
    document bodies.
    """

    def __init__(self, session_factory: Any) -> None:
        self.session_factory = session_factory

    def put(self, query: str, hits: list[dict[str, Any]]) -> list[str]:
        """Store per-document cache rows for a query. Idempotent by key."""
        query_hash = query_hash_for(query)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for hit in hits:
            grouped.setdefault(hit["document_id"], []).append(
                {"chunk_id": hit["chunk_id"], "distance": hit["distance"]}
            )
        keys: list[str] = []
        with self.session_factory() as session:
            for document_id, entries in grouped.items():
                key = cache_key_for(query_hash, document_id)
                row = session.query(CacheEntry).filter_by(cache_key=key).first()
                if row is None:
                    row = CacheEntry(
                        cache_key=key,
                        document_id=document_id,
                        query_hash=query_hash,
                        result_ids=json.dumps(entries, sort_keys=True),
                        created_at=utcnow(),
                    )
                    session.add(row)
                else:
                    row.result_ids = json.dumps(entries, sort_keys=True)
                    row.invalidated_at = None
                artifact_id = f"{document_id}:cache:{key}"
                if session.query(DerivedArtifact).filter_by(id=artifact_id).first() is None:
                    # The parent source row may not exist for legacy documents
                    # never yet deleted; create it so the lineage link (and
                    # foreign-key integrity) always holds.
                    source_id = f"{document_id}:source"
                    if session.query(DerivedArtifact).filter_by(id=source_id).first() is None:
                        session.add(
                            DerivedArtifact(
                                id=source_id,
                                document_id=document_id,
                                artifact_type="source_file",
                                backend="filesystem",
                                identifier=source_id,
                                parent_id=None,
                                status="active",
                                created_at=utcnow(),
                                updated_at=utcnow(),
                            )
                        )
                    session.add(
                        DerivedArtifact(
                            id=artifact_id,
                            document_id=document_id,
                            artifact_type="app_cache_entry",
                            backend="app_cache",
                            identifier=key,
                            parent_id=f"{document_id}:source",
                            status="active",
                            created_at=utcnow(),
                            updated_at=utcnow(),
                        )
                    )
                keys.append(key)
            session.commit()
        return sorted(keys)

    def get(self, query: str) -> list[dict[str, Any]]:
        """Return active cached chunk references for a query hash."""
        query_hash = query_hash_for(query)
        with self.session_factory() as session:
            rows = (
                session.query(CacheEntry)
                .filter(
                    CacheEntry.query_hash == query_hash,
                    CacheEntry.invalidated_at.is_(None),
                )
                .all()
            )
            cached: list[dict[str, Any]] = []
            for row in rows:
                # Skip malformed rows instead of failing the whole lookup:
                # one corrupt entry must not break search for a query hash.
                try:
                    entries = json.loads(row.result_ids)
                except (ValueError, TypeError):
                    continue
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not _valid_reference(entry):
                        continue
                    cached.append(
                        {
                            "document_id": row.document_id,
                            "chunk_id": entry["chunk_id"],
                            "distance": entry["distance"],
                        }
                    )
            return cached

    def invalidate_document(self, document_id: str) -> dict[str, Any]:
        """Mark a document's cache rows invalidated. Returns evidence counts."""
        with self.session_factory() as session:
            rows = (
                session.query(CacheEntry)
                .filter(
                    CacheEntry.document_id == document_id,
                    CacheEntry.invalidated_at.is_(None),
                )
                .all()
            )
            keys = sorted(row.cache_key for row in rows)
            timestamp = utcnow()
            for row in rows:
                row.invalidated_at = timestamp
            session.commit()
            return {"invalidated_cache_keys": keys, "invalidated_count": len(keys)}

    def residuals(self, document_id: str) -> dict[str, Any]:
        """Active (non-invalidated) cache rows still linked to a document."""
        with self.session_factory() as session:
            rows = (
                session.query(CacheEntry)
                .filter(
                    CacheEntry.document_id == document_id,
                    CacheEntry.invalidated_at.is_(None),
                )
                .all()
            )
            keys = sorted(row.cache_key for row in rows)
            return {"residual_cache_keys": keys, "residual_count": len(keys)}
