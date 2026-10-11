from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import main
from backend.app.db.session import Base, ensure_schema
from backend.app.models.deletion import DeletionOperation
from backend.app.models.document import DocumentRecord
from backend.app.models.lineage import CacheEntry, DerivedArtifact
from backend.app.services.adapters import registered_adapters
from backend.app.services.cache_store import AppCache
from backend.app.services.document_service import DocumentService
from backend.app.services.vector_store import ChromaVectorStore

pytestmark = pytest.mark.integration


def _client_for_peer(app, peer_address: str) -> TestClient:
    async def peer_app(scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "client": (peer_address, 50000)}
        await app(scope, receive, send)

    return TestClient(peer_app)


@pytest.fixture
def app_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_path = tmp_path / "metadata.sqlite3"
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    collection_name = f"lineage-{uuid.uuid4().hex}"
    vector_store = ChromaVectorStore(tmp_path / "chroma", collection_name=collection_name)
    service = DocumentService(
        session_factory=session_factory,
        vector_store=vector_store,
        upload_dir=tmp_path / "uploads",
        delete_auth_token="test-delete-token",
    )
    monkeypatch.setattr(main, "service", service)
    client = _client_for_peer(main.app, "127.0.0.1")
    yield client, service, session_factory
    client.close()
    engine.dispose()


def _artifact_rows(session_factory, document_id: str) -> list[DerivedArtifact]:
    with session_factory() as session:
        return (
            session.query(DerivedArtifact)
            .filter_by(document_id=document_id)
            .order_by(DerivedArtifact.id)
            .all()
        )


def test_only_real_adapters_are_registered(app_environment) -> None:
    _, service, _ = app_environment
    adapters = registered_adapters(service.cache)
    assert set(adapters) == {"app_cache"}
    assert adapters["app_cache"].layer == "app_cache"


def test_ingest_records_lineage_with_exact_vector_ids(app_environment) -> None:
    client, service, session_factory = app_environment
    marker = f"ghost-erase-lineage-{uuid.uuid4().hex}"
    uploaded = client.post(
        "/api/v1/documents/upload",
        files={"file": ("lineage.txt", f"Lineage marker {marker} synthetic body.".encode(), "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    target = uploaded.json()

    rows = _artifact_rows(session_factory, target["id"])
    by_type: dict[str, list[DerivedArtifact]] = {}
    for row in rows:
        by_type.setdefault(row.artifact_type, []).append(row)
    assert [row.identifier for row in by_type["source_file"]] != []
    assert sorted(row.identifier for row in by_type["chroma_chunk"]) == sorted(target["chunk_ids"])
    assert all(
        row.backend == f"chromadb:{service.vector_store.collection.name}"
        for row in by_type["chroma_chunk"]
    )
    assert len(by_type["sql_content"]) == 1
    source_id = f"{target['id']}:source"
    assert all(row.parent_id == source_id for row in by_type["chroma_chunk"] + by_type["sql_content"])
    assert all(row.status == "active" for row in rows)
    assert marker not in json.dumps(
        [
            {
                "id": row.id,
                "type": row.artifact_type,
                "backend": row.backend,
                "identifier": row.identifier,
                "parent": row.parent_id,
                "status": row.status,
            }
            for row in rows
        ]
    )


def test_lineage_backfill_is_idempotent_for_legacy_documents(app_environment) -> None:
    _, service, session_factory = app_environment
    legacy_id = str(uuid.uuid4())
    chunk_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    with session_factory() as session:
        session.add(
            DocumentRecord(
                id=legacy_id,
                filename="legacy.txt",
                source="upload",
                content="legacy body",
                content_hash="hash",
                chunk_ids=json.dumps(chunk_ids),
                source_file_path=None,
                status="indexed",
                created_at="2026-01-01T00:00:00+00:00",
            )
        )
        session.commit()
    assert _artifact_rows(session_factory, legacy_id) == []
    with session_factory() as session:
        DocumentService._ensure_lineage(
            session, legacy_id, "legacy.txt", None, chunk_ids, service.vector_store.collection.name
        )
        session.commit()
    first = _artifact_rows(session_factory, legacy_id)
    assert len(first) == 1 + len(chunk_ids) + 1
    with session_factory() as session:
        DocumentService._ensure_lineage(
            session, legacy_id, "legacy.txt", None, chunk_ids, service.vector_store.collection.name
        )
        session.commit()
    assert len(_artifact_rows(session_factory, legacy_id)) == len(first)


def test_cache_put_get_invalidate_is_idempotent(app_environment) -> None:
    _, service, session_factory = app_environment
    cache = AppCache(session_factory)
    hits = [
        {"document_id": "doc-a", "chunk_id": "chunk-1", "distance": 0.1},
        {"document_id": "doc-a", "chunk_id": "chunk-2", "distance": 0.2},
    ]
    first_keys = cache.put("some query", hits)
    second_keys = cache.put("some query", hits)
    assert first_keys == second_keys
    assert len(first_keys) == 1
    assert cache.get("some query") == [
        {"document_id": "doc-a", "chunk_id": "chunk-1", "distance": 0.1},
        {"document_id": "doc-a", "chunk_id": "chunk-2", "distance": 0.2},
    ]
    assert cache.residuals("doc-a")["residual_count"] == 1
    assert cache.invalidate_document("doc-a")["invalidated_count"] == 1
    assert cache.get("some query") == []
    assert cache.residuals("doc-a")["residual_count"] == 0
    # Re-put after invalidation reactivates the same key.
    assert cache.put("some query", hits) == first_keys
    assert cache.residuals("doc-a")["residual_count"] == 1


def test_migration_preserves_legacy_data_and_adds_new_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    raw = sqlite3.connect(str(database_path))
    raw.execute(
        "CREATE TABLE documents (id VARCHAR PRIMARY KEY, filename VARCHAR, source VARCHAR NOT NULL,"
        " content TEXT, content_hash VARCHAR NOT NULL, chunk_ids TEXT NOT NULL,"
        " source_file_path TEXT, status VARCHAR, created_at VARCHAR, deleted_at VARCHAR)"
    )
    raw.execute(
        "CREATE TABLE deletion_operations (id VARCHAR PRIMARY KEY, document_id VARCHAR NOT NULL,"
        " chunk_ids TEXT NOT NULL, status VARCHAR NOT NULL, created_at VARCHAR NOT NULL,"
        " completed_at VARCHAR)"
    )
    raw.execute(
        "CREATE TABLE deletion_checks (id VARCHAR PRIMARY KEY, operation_id VARCHAR NOT NULL,"
        " document_id VARCHAR NOT NULL, chunk_ids TEXT NOT NULL, layer VARCHAR NOT NULL,"
        " method VARCHAR NOT NULL, status VARCHAR NOT NULL, actual_result TEXT NOT NULL,"
        " error_detail TEXT, checked_at VARCHAR NOT NULL)"
    )
    raw.execute(
        "INSERT INTO documents VALUES ('legacy-doc-1', 'legacy.txt', 'upload', 'legacy body',"
        " 'deadbeef', '[]', NULL, 'indexed', '2026-01-01T00:00:00+00:00', NULL)"
    )
    raw.execute(
        "INSERT INTO deletion_operations VALUES ('legacy-op-1', 'legacy-doc-1', '[]', 'fail',"
        " '2026-01-02T00:00:00+00:00', '2026-01-02T00:01:00+00:00')"
    )
    raw.commit()
    raw.close()

    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    ensure_schema(engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with session_factory() as session:
        legacy_doc = session.query(DocumentRecord).filter_by(id="legacy-doc-1").one()
        assert legacy_doc.content == "legacy body"
        assert legacy_doc.status == "indexed"
        assert session.query(DeletionOperation).filter_by(id="legacy-op-1").one().status == "fail"
        assert session.query(DerivedArtifact).count() == 0
        assert session.query(CacheEntry).count() == 0
    engine.dispose()


class FailingCacheRemoveAdapter:
    layer = "app_cache"

    def __init__(self, delegate) -> None:
        self.delegate = delegate

    def remove(self, document_id: str) -> dict:
        raise RuntimeError("synthetic cache invalidate failure")

    def residuals(self, document_id: str) -> dict:
        return self.delegate.residuals(document_id)


class ResidualsUnavailableCacheAdapter:
    layer = "app_cache"

    def __init__(self, delegate) -> None:
        self.delegate = delegate

    def remove(self, document_id: str) -> dict:
        return self.delegate.remove(document_id)

    def residuals(self, document_id: str) -> dict:
        raise RuntimeError("synthetic cache residuals unavailable")


def test_cache_residual_is_detected_then_remediated_on_retry(app_environment) -> None:
    client, service, session_factory = app_environment
    marker = f"ghost-erase-cacheres-{uuid.uuid4().hex}"
    content = f"Cache residual marker {marker}. Synthetic body for the stale-cache demo."
    uploaded = client.post(
        "/api/v1/documents/upload",
        files={"file": ("cachedoc.txt", content.encode(), "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    target = uploaded.json()
    found = client.post("/api/v1/search", json={"query": marker, "limit": 10})
    assert any(hit["document_id"] == target["id"] for hit in found.json()["results"])
    assert service.cache.residuals(target["id"])["residual_count"] == 1

    real_adapter = service.cache_adapter
    service.cache_adapter = FailingCacheRemoveAdapter(service.cache_adapter)
    try:
        first = client.post(
            f"/api/v1/documents/{target['id']}/delete",
            headers={"X-Delete-Token": "test-delete-token"},
        ).json()
    finally:
        service.cache_adapter = real_adapter
    # Source and vector records are gone, but the stale cache entry remains:
    # the report must fail, not pass.
    assert first["status"] == "fail"
    cache_check = next(check for check in first["checks"] if check["layer"] == "app_cache")
    assert cache_check["status"] == "fail"
    assert cache_check["error_detail"] is not None
    assert cache_check["actual_result"]["residual_count"] == 1
    assert len(cache_check["actual_result"]["residual_cache_keys"]) == 1
    assert next(
        check for check in first["checks"] if check["layer"] == "source_file"
    )["status"] == "pass"
    assert marker not in json.dumps(first)
    with session_factory() as session:
        stored = session.query(DocumentRecord).filter_by(id=target["id"]).one()
        assert stored.content == content
        assert stored.status == "deletion_failed"
    assert [row.status for row in _artifact_rows(session_factory, target["id"]) if row.artifact_type == "app_cache_entry"] == [
        "residual_detected"
    ]

    # Remediate the stale entry and retry: the full report must now pass,
    # reusing source-removal and vector-removal evidence without contradiction.
    second = client.post(
        f"/api/v1/documents/{target['id']}/delete",
        headers={"X-Delete-Token": "test-delete-token"},
    ).json()
    assert second["status"] == "pass"
    assert second["operation_id"] != first["operation_id"]
    assert all(check["status"] == "pass" for check in second["checks"])
    assert len(second["checks"]) == 6
    assert marker not in json.dumps(second)
    with session_factory() as session:
        assert session.query(DocumentRecord).filter_by(id=target["id"]).one().content == ""
        operations = (
            session.query(DeletionOperation)
            .filter_by(document_id=target["id"])
            .order_by(DeletionOperation.created_at)
            .all()
        )
        # History is preserved: the earlier failure is not overwritten.
        assert [operation.status for operation in operations] == ["fail", "pass"]
        assert all(
            row.status == "deleted_verified"
            for row in _artifact_rows(session_factory, target["id"])
        )


def test_cache_invalidate_failure_reports_fail_and_residuals_outage_reports_blocked(
    app_environment,
) -> None:
    client, service, session_factory = app_environment
    marker = f"ghost-erase-cachefail-{uuid.uuid4().hex}"
    uploaded = client.post(
        "/api/v1/documents/upload",
        files={"file": ("cachefail.txt", f"Cache failure marker {marker} body.".encode(), "text/plain")},
    )
    target = uploaded.json()
    client.post("/api/v1/search", json={"query": marker, "limit": 10})

    real_adapter = service.cache_adapter
    service.cache_adapter = FailingCacheRemoveAdapter(service.cache_adapter)
    try:
        failed = client.post(
            f"/api/v1/documents/{target['id']}/delete",
            headers={"X-Delete-Token": "test-delete-token"},
        ).json()
    finally:
        service.cache_adapter = real_adapter
    assert failed["status"] == "fail"
    assert next(
        check for check in failed["checks"] if check["layer"] == "app_cache"
    )["status"] == "fail"
    assert marker not in json.dumps(failed)

    service.cache_adapter = ResidualsUnavailableCacheAdapter(service.cache_adapter)
    try:
        blocked = client.post(
            f"/api/v1/documents/{target['id']}/delete",
            headers={"X-Delete-Token": "test-delete-token"},
        ).json()
    finally:
        service.cache_adapter = real_adapter
    # Invalidation now succeeds but residuals cannot be inspected: blocked,
    # never successful.
    assert blocked["status"] == "blocked"
    assert next(
        check for check in blocked["checks"] if check["layer"] == "app_cache"
    )["status"] == "blocked"
    assert marker not in json.dumps(blocked)
    with session_factory() as session:
        assert session.query(DocumentRecord).filter_by(id=target["id"]).one().content != ""


def test_report_lineage_and_cache_persist_across_restart(app_environment) -> None:
    client, service, session_factory = app_environment
    marker = f"ghost-erase-restart-{uuid.uuid4().hex}"
    uploaded = client.post(
        "/api/v1/documents/upload",
        files={"file": ("restart.txt", f"Restart marker {marker} body.".encode(), "text/plain")},
    )
    target = uploaded.json()
    client.post("/api/v1/search", json={"query": marker, "limit": 10})
    report = client.post(
        f"/api/v1/documents/{target['id']}/delete",
        headers={"X-Delete-Token": "test-delete-token"},
    ).json()
    assert report["status"] == "pass"

    restarted_vector_store = ChromaVectorStore(
        service.vector_store.path,
        collection_name=service.vector_store.collection.name,
    )
    restarted_service = DocumentService(
        session_factory=session_factory,
        vector_store=restarted_vector_store,
        upload_dir=service.upload_dir,
        delete_auth_token="test-delete-token",
    )
    replacement = main.service
    main.service = restarted_service
    try:
        persisted = client.get(f"/api/v1/deletion-operations/{report['operation_id']}")
        assert persisted.status_code == 200
        assert persisted.json()["status"] == "pass"
        assert len(persisted.json()["checks"]) == 6
        rows = _artifact_rows(session_factory, target["id"])
        assert rows != []
        assert {row.status for row in rows} == {"deleted_verified"}
        assert restarted_service.cache.residuals(target["id"])["residual_count"] == 0
        assert next(
            item for item in restarted_service.list_documents() if item["id"] == target["id"]
        )["status"] == "deleted"
    finally:
        main.service = replacement


def test_document_lineage_endpoint_returns_known_artifacts(app_environment) -> None:
    client, _, _ = app_environment
    marker = f"ghost-erase-detail-{uuid.uuid4().hex}"
    uploaded = client.post(
        "/api/v1/documents/upload",
        files={"file": ("detail.txt", f"Detail marker {marker} body.".encode(), "text/plain")},
    )
    target = uploaded.json()
    client.post("/api/v1/search", json={"query": marker, "limit": 10})

    detail = client.get(f"/api/v1/documents/{target['id']}/lineage")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["document"]["id"] == target["id"]
    assert payload["document"]["chunk_count"] == len(target["chunk_ids"])
    types = {artifact["artifact_type"] for artifact in payload["artifacts"]}
    assert {"source_file", "chroma_chunk", "sql_content"} <= types
    assert len(payload["cache_entries"]) == 1
    assert payload["cache_entries"][0]["invalidated_at"] is None
    assert "lineage_note" in payload
    assert marker not in detail.text

    missing = client.get(f"/api/v1/documents/{uuid.uuid4()}/lineage")
    assert missing.status_code == 404


def test_foreign_key_enforcement_rejects_orphan_rows(tmp_path: Path) -> None:
    from sqlalchemy import event
    from sqlalchemy.exc import IntegrityError

    from backend.app.models.lineage import CacheEntry, DerivedArtifact

    database_path = tmp_path / "fk.sqlite3"
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _enforce_foreign_keys(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with session_factory() as session:
        session.add(
            DocumentRecord(
                id="fk-doc",
                filename="fk.txt",
                source="upload",
                content="fk body",
                content_hash="hash",
                chunk_ids="[]",
                source_file_path=None,
                status="indexed",
                created_at="2026-01-01T00:00:00+00:00",
            )
        )
        session.commit()
        session.add(
            CacheEntry(
                cache_key="q:fk-doc",
                document_id="fk-doc",
                query_hash="q",
                result_ids="[]",
                created_at="2026-01-01T00:00:00+00:00",
            )
        )
        session.commit()
        session.add(
            CacheEntry(
                cache_key="q:ghost",
                document_id="no-such-document",
                query_hash="q",
                result_ids="[]",
                created_at="2026-01-01T00:00:00+00:00",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        session.add(
            DerivedArtifact(
                id="ghost:cache:q",
                document_id="fk-doc",
                artifact_type="app_cache_entry",
                backend="app_cache",
                identifier="q",
                parent_id="no-such-parent",
                status="active",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    engine.dispose()


def test_cache_table_outage_is_recorded_as_failure(app_environment) -> None:
    from backend.app.services.adapters import CacheDeletionAdapter
    from backend.app.services.cache_store import AppCache

    client, service, session_factory = app_environment
    marker = f"ghost-erase-cacheout-{uuid.uuid4().hex}"
    uploaded = client.post(
        "/api/v1/documents/upload",
        files={"file": ("cacheout.txt", f"Cache outage marker {marker} body.".encode(), "text/plain")},
    )
    target = uploaded.json()

    def broken_factory():
        raise RuntimeError("synthetic cache store outage")

    real_adapter = service.cache_adapter
    service.cache_adapter = CacheDeletionAdapter(AppCache(broken_factory))
    try:
        report = client.post(
            f"/api/v1/documents/{target['id']}/delete",
            headers={"X-Delete-Token": "test-delete-token"},
        ).json()
    finally:
        service.cache_adapter = real_adapter
    assert report["status"] == "fail"
    cache_check = next(check for check in report["checks"] if check["layer"] == "app_cache")
    assert cache_check["status"] == "fail"
    assert cache_check["error_detail"] is not None
    assert marker not in json.dumps(report)
    with session_factory() as session:
        stored = session.query(DocumentRecord).filter_by(id=target["id"]).one()
        assert stored.status == "deletion_failed"
        assert marker in stored.content


def test_stale_cache_acceptance_lifecycle(app_environment) -> None:
    """Signature demo as an executable test: canary upload, real retrieval,
    persisted cache entry, deletion with preserved cache, residual failure
    with evidence, normal-path remediation, fresh re-verification, restart
    persistence. Synthetic data only; markers must not leak into evidence.
    """
    import math

    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

    client, service, session_factory = app_environment
    marker = f"ghost-erase-accept-{uuid.uuid4().hex}"
    body = (
        f"Acceptance canary {marker}. Harmless synthetic text. "
        + "Padding for a second chunk. " * 60
    )

    uploaded = client.post(
        "/api/v1/documents/upload",
        files={"file": ("accept.txt", body.encode(), "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    target = uploaded.json()
    assert len(target["chunk_ids"]) > 1

    # Real configured embedding path and ChromaDB index in use.
    assert isinstance(
        service.vector_store.collection._embedding_function, DefaultEmbeddingFunction
    )
    found = client.post("/api/v1/search", json={"query": marker, "limit": 10})
    assert found.status_code == 200
    hits = [hit for hit in found.json()["results"] if hit["document_id"] == target["id"]]
    assert hits != []
    assert any(marker in hit["content"] for hit in hits)
    assert all(isinstance(hit["distance"], float) and math.isfinite(hit["distance"]) for hit in hits)

    # An actual persisted cache entry from the real retrieval result.
    lineage = client.get(f"/api/v1/documents/{target['id']}/lineage").json()
    assert len(lineage["cache_entries"]) == 1
    assert lineage["cache_entries"][0]["invalidated_at"] is None
    assert service.cache.residuals(target["id"])["residual_count"] == 1

    # Delete while preserving the cache entry: the operation must detect the
    # residual and must not report all layers verified.
    real_adapter = service.cache_adapter

    class PreservedCacheAdapter:
        layer = "app_cache"

        def remove(self, document_id: str) -> dict:
            return {"invalidated_cache_keys": [], "invalidated_count": 0}

        def residuals(self, document_id: str) -> dict:
            return real_adapter.residuals(document_id)

    service.cache_adapter = PreservedCacheAdapter()
    try:
        first = client.post(
            f"/api/v1/documents/{target['id']}/delete",
            headers={"X-Delete-Token": "test-delete-token"},
        ).json()
    finally:
        service.cache_adapter = real_adapter
    assert first["status"] == "fail"
    assert first["kind"] == "deletion"
    assert first["served_from_history"] is False
    cache_check = next(check for check in first["checks"] if check["layer"] == "app_cache")
    assert cache_check["status"] == "fail"
    assert cache_check["method"] == "cache.invalidate(document_id) then cache.residuals(document_id)"
    assert cache_check["checked_at"] != ""
    assert cache_check["actual_result"]["residual_count"] == 1
    assert len(cache_check["actual_result"]["residual_cache_keys"]) == 1
    # Unsupported layers stay unverified, never passed.
    assert "backups" in first["scope"]["not_examined"]
    assert first["scope"]["physical_erasure_verified"] is False
    assert marker not in json.dumps(first)
    with session_factory() as session:
        stored = session.query(DocumentRecord).filter_by(id=target["id"]).one()
        assert stored.content != ""
        assert stored.status == "deletion_failed"

    # Remediate through the normal cache invalidation path.
    remediated = service.cache_adapter.remove(target["id"])
    assert remediated["invalidated_count"] == 1
    assert service.cache.residuals(target["id"])["residual_count"] == 0

    # Fresh read-only re-verification: new persisted run, cache now passing.
    reverified = client.post(f"/api/v1/documents/{target['id']}/verify").json()
    assert reverified["operation_id"] != first["operation_id"]
    assert reverified["kind"] == "verification"
    assert next(
        check for check in reverified["checks"] if check["layer"] == "app_cache"
    )["status"] == "pass"
    assert marker not in json.dumps(reverified)

    # Complete the deletion; the full pass follows remediation.
    second = client.post(
        f"/api/v1/documents/{target['id']}/delete",
        headers={"X-Delete-Token": "test-delete-token"},
    ).json()
    assert second["status"] == "pass"
    assert len(second["checks"]) == 6
    with session_factory() as session:
        statuses = sorted(
            operation.status
            for operation in session.query(DeletionOperation)
            .filter_by(document_id=target["id"])
            .all()
        )
        # Append-only history: fail, then verification runs, then pass.
        assert statuses[0] == "fail"
        assert statuses[-1] == "pass"
        assert session.query(DeletionOperation).filter_by(document_id=target["id"]).count() == 3

    # Restart: source/deletion/report/lineage state persists as applicable.
    restarted_service = DocumentService(
        session_factory=session_factory,
        vector_store=ChromaVectorStore(
            service.vector_store.path,
            collection_name=service.vector_store.collection.name,
        ),
        upload_dir=service.upload_dir,
        delete_auth_token="test-delete-token",
    )
    replacement = main.service
    main.service = restarted_service
    try:
        for operation_id in (first["operation_id"], reverified["operation_id"], second["operation_id"]):
            reread = client.get(f"/api/v1/deletion-operations/{operation_id}")
            assert reread.status_code == 200
        rows = _artifact_rows(session_factory, target["id"])
        assert rows != []
        assert {row.status for row in rows} == {"deleted_verified"}
        assert restarted_service.cache.residuals(target["id"])["residual_count"] == 0
        assert marker not in json.dumps([row.id for row in rows])
    finally:
        main.service = replacement


def test_guided_demo_is_guarded_and_preserves_residual_evidence(app_environment, monkeypatch) -> None:
    from backend.app.core.config import settings

    client, service, session_factory = app_environment
    monkeypatch.setattr(settings, "demo_mode", False)
    assert client.post(
        "/api/v1/demo/runs",
        headers={"X-Delete-Token": "test-delete-token"},
    ).status_code == 404
    assert client.get("/api/v1/documents").json() == []

    monkeypatch.setattr(settings, "demo_mode", True)
    unauthorized = client.post("/api/v1/demo/runs")
    assert unauthorized.status_code == 403
    assert client.get("/api/v1/documents").json() == []

    started = client.post(
        "/api/v1/demo/runs",
        headers={"X-Delete-Token": "test-delete-token"},
    )
    assert started.status_code == 200, started.text
    run = started.json()
    initial = run["initial_report"]
    assert run["demo_mode"] is True
    assert run["document_id"] == initial["document_id"]
    assert run["query"].startswith("synthetic canary ghost-erase-demo-")
    assert initial["status"] == "fail"
    assert any(
        check["layer"] == "app_cache"
        and check["status"] == "fail"
        and check["actual_result"]["residual_count"] == 1
        for check in initial["checks"]
    )
    assert run["before"]["document"]["source"] == "guided_demo"
    assert all(
        artifact["state_before"] == "active"
        for artifact in initial["scope"]["pre_action_artifacts"]
    )
    assert initial["scope"]["physical_erasure_verified"] is False
    assert "unknown_or_unregistered_copies" in initial["scope"]["not_examined"]
    assert run["query"].split()[-1] not in json.dumps(initial)

    initial_operation_count = len(client.get("/api/v1/deletion-operations").json())
    refused = client.post(
        f"/api/v1/demo/runs/{initial['operation_id']}/remediate",
        headers={"X-Delete-Token": "wrong-token"},
    )
    assert refused.status_code == 403
    assert len(client.get("/api/v1/deletion-operations").json()) == initial_operation_count

    remediated = client.post(
        f"/api/v1/demo/runs/{initial['operation_id']}/remediate",
        headers={"X-Delete-Token": "test-delete-token"},
    )
    assert remediated.status_code == 200, remediated.text
    outcome = remediated.json()
    assert outcome["remediation_report"]["kind"] == "remediation"
    assert outcome["remediation_report"]["status"] == "pass"
    assert outcome["remediation_report"]["checks"][0]["actual_result"]["authorization"] == "delete_token_validated"
    assert outcome["pre_completion_verification"]["kind"] == "verification"
    assert outcome["pre_completion_verification"]["status"] == "fail"
    assert next(
        check for check in outcome["pre_completion_verification"]["checks"]
        if check["layer"] == "app_cache"
    )["status"] == "pass"
    assert outcome["final_report"]["kind"] == "deletion"
    assert outcome["final_report"]["status"] == "pass"
    assert all(check["status"] == "pass" for check in outcome["final_report"]["checks"])
    assert outcome["fresh_verification"]["kind"] == "verification"
    assert outcome["fresh_verification"]["status"] == "blocked"
    assert next(
        check for check in outcome["fresh_verification"]["checks"]
        if check["layer"] == "application_retrieval"
    )["status"] == "blocked"
    assert all(
        check["status"] == "pass"
        for check in outcome["fresh_verification"]["checks"]
        if check["layer"] in {"source_file", "chromadb_api", "app_cache", "sql_metadata"}
    )
    assert outcome["artifact_comparison"]
    assert all(
        row["state_after"] == "deleted_verified"
        for row in outcome["artifact_comparison"]
    )
    assert run["query"].split()[-1] not in json.dumps(outcome)

    with session_factory() as session:
        operations = (
            session.query(DeletionOperation)
            .filter_by(document_id=run["document_id"])
            .order_by(DeletionOperation.created_at, DeletionOperation.id)
            .all()
        )
        assert [operation.kind for operation in operations] == [
            "deletion",
            "remediation",
            "verification",
            "deletion",
            "verification",
        ]
        assert all(operation.scope_snapshot for operation in operations)
        assert all(operation.result_snapshot for operation in operations)
        original = service.deletion_report(initial["operation_id"])
        assert original["status"] == "fail"
        assert original["scope"]["pre_action_artifacts"]


def test_service_constructs_adapter_from_registry(
    app_environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from backend.app.services import document_service as service_module

    _, service, _ = app_environment
    sentinel = SimpleNamespace(layer="app_cache")
    monkeypatch.setattr(
        service_module, "registered_adapters", lambda cache: {"app_cache": sentinel}
    )
    rebuilt = service_module.DocumentService(
        session_factory=service.session_factory,
        vector_store=service.vector_store,
        upload_dir=service.upload_dir,
        delete_auth_token="test-delete-token",
    )
    assert rebuilt.cache_adapter is sentinel


def test_malformed_cache_rows_do_not_break_reads(app_environment) -> None:
    from backend.app.services.cache_store import count_results, query_hash_for

    client, service, session_factory = app_environment
    marker = f"ghost-erase-malformed-{uuid.uuid4().hex}"
    document = service.create_document(f"Malformed-row marker {marker} body.", filename="rows.txt")
    good = service.cache.put(
        "malformed query",
        [{"document_id": document["id"], "chunk_id": document["chunk_ids"][0], "distance": 0.1}],
    )
    assert len(good) == 1
    query_hash = query_hash_for("malformed query")
    with session_factory() as session:
        session.add(
            CacheEntry(
                cache_key=f"{query_hash}:broken-json",
                document_id=document["id"],
                query_hash=query_hash,
                result_ids="not-json{{{",
                created_at="2026-01-01T00:00:00+00:00",
            )
        )
        session.add(
            CacheEntry(
                cache_key=f"{query_hash}:wrong-shape",
                document_id=document["id"],
                query_hash=query_hash,
                result_ids='{"chunk_id": "x"}',
                created_at="2026-01-01T00:00:00+00:00",
            )
        )
        session.add(
            CacheEntry(
                cache_key=f"{query_hash}:missing-keys",
                document_id=document["id"],
                query_hash=query_hash,
                result_ids='[{"chunk_id": "y"}]',
                created_at="2026-01-01T00:00:00+00:00",
            )
        )
        session.commit()

    assert service.cache.get("malformed query") == [
        {"document_id": document["id"], "chunk_id": document["chunk_ids"][0], "distance": 0.1}
    ]
    assert count_results("not-json{{{") == 0
    assert count_results('{"chunk_id": "x"}') == 0
    assert count_results('[{"chunk_id": "y"}]') == 0

    searched = client.post("/api/v1/search", json={"query": "malformed query", "limit": 5})
    assert searched.status_code == 200
    detail = client.get(f"/api/v1/documents/{document['id']}/lineage")
    assert detail.status_code == 200
    assert marker not in json.dumps(detail.json())
