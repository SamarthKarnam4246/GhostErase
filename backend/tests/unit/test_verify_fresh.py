from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import main
from backend.app.db.session import Base
from backend.app.models.deletion import DeletionOperation
from backend.app.models.document import DocumentRecord
from backend.app.services.document_service import DocumentService
from backend.app.services.vector_store import ChromaVectorStore

pytestmark = pytest.mark.integration


def _client_for_peer(peer_address: str) -> TestClient:
    from backend.app.main import app

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
    vector_store = ChromaVectorStore(
        tmp_path / "chroma", collection_name=f"verify-{uuid.uuid4().hex}"
    )
    service = DocumentService(
        session_factory=session_factory,
        vector_store=vector_store,
        upload_dir=tmp_path / "uploads",
        delete_auth_token="test-delete-token",
    )
    monkeypatch.setattr(main, "service", service)
    client = _client_for_peer("127.0.0.1")
    yield client, service, session_factory
    client.close()
    engine.dispose()


class OutageVectorStore:
    def __init__(self, delegate: ChromaVectorStore) -> None:
        self.delegate = delegate

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    def document_chunks(self, document_id: str) -> dict:
        raise RuntimeError("synthetic chroma residual outage")

    def query(self, query_text: str, limit: int = 5) -> list[dict]:
        raise RuntimeError("synthetic chroma query outage")


class QueryOutageVectorStore:
    def __init__(self, delegate: ChromaVectorStore) -> None:
        self.delegate = delegate

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    def query(self, query_text: str, limit: int = 5) -> list[dict]:
        raise RuntimeError("synthetic chroma query outage")


def _upload(client: TestClient, marker: str, filename: str = "doc.txt") -> dict:
    uploaded = client.post(
        "/api/v1/documents/upload",
        files={"file": (filename, f"Marker {marker} synthetic body.".encode(), "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    return uploaded.json()


def _delete(client: TestClient, document_id: str) -> dict:
    return client.post(
        f"/api/v1/documents/{document_id}/delete",
        headers={"X-Delete-Token": "test-delete-token"},
    ).json()


def test_fresh_verify_of_deleted_doc_rereads_backends_without_mutating(
    app_environment,
) -> None:
    client, service, session_factory = app_environment
    marker = f"ghost-erase-vfresh-{uuid.uuid4().hex}"
    target = _upload(client, marker)
    report = _delete(client, target["id"])
    assert report["status"] == "pass"
    before = client.get("/api/v1/dashboard").json()

    verified = client.post(f"/api/v1/documents/{target['id']}/verify").json()
    assert verified["operation_id"] != report["operation_id"]
    assert verified["kind"] == "verification"
    assert verified["served_from_history"] is False
    assert len(verified["checks"]) == 5
    by_layer = {check["layer"]: check["status"] for check in verified["checks"]}
    assert by_layer == {
        "source_file": "pass",
        "chromadb_api": "pass",
        "app_cache": "pass",
        "application_retrieval": "blocked",
        "sql_metadata": "pass",
    }
    # Four storage layers verified absent; the probe cannot run without
    # retained content, so the honest overall state is blocked, not pass.
    assert verified["status"] == "blocked"
    assert marker not in json.dumps(verified)

    reread = client.get(f"/api/v1/deletion-operations/{verified['operation_id']}")
    assert reread.status_code == 200
    assert reread.json()["kind"] == "verification"

    # Nothing was deleted, cleared, or invalidated by verification.
    assert not Path(service.upload_dir, target["id"], "doc.txt").exists()
    assert service.vector_store.document_chunks(target["id"])["ids"] == []
    assert service.cache.residuals(target["id"])["residual_count"] == 0
    with session_factory() as session:
        stored = session.query(DocumentRecord).filter_by(id=target["id"]).one()
        assert stored.content == ""
        assert stored.status == "deleted"
    after = client.get("/api/v1/dashboard").json()
    assert after["counts"] == before["counts"]
    assert after["recent_operations"] == before["recent_operations"]


def test_fresh_verify_of_indexed_doc_reports_present_without_mutating(
    app_environment,
) -> None:
    client, service, session_factory = app_environment
    marker = f"ghost-erase-vpresent-{uuid.uuid4().hex}"
    target = _upload(client, marker)
    verified = client.post(f"/api/v1/documents/{target['id']}/verify").json()
    assert verified["kind"] == "verification"
    assert verified["status"] == "fail"
    by_layer = {check["layer"]: check["status"] for check in verified["checks"]}
    assert by_layer["source_file"] == "fail"
    assert by_layer["chromadb_api"] == "fail"
    assert by_layer["application_retrieval"] == "fail"
    assert by_layer["sql_metadata"] == "fail"
    assert marker not in json.dumps(verified)
    with session_factory() as session:
        stored = session.query(DocumentRecord).filter_by(id=target["id"]).one()
        assert marker in stored.content
        assert stored.status == "indexed"
    assert Path(service.upload_dir, target["id"], "doc.txt").is_file()
    assert service.vector_store.document_chunks(target["id"])["ids"] == target["chunk_ids"]


def test_verify_unknown_document_records_nothing(app_environment) -> None:
    client, _, session_factory = app_environment
    response = client.post(f"/api/v1/documents/{uuid.uuid4()}/verify")
    assert response.status_code == 200
    assert response.json()["status"] == "not_verified"
    assert "operation_id" not in response.json()
    with session_factory() as session:
        assert session.query(DeletionOperation).count() == 0


def test_verify_maps_backend_outage_to_blocked(app_environment) -> None:
    client, service, session_factory = app_environment
    target = _upload(client, f"ghost-erase-voutage-{uuid.uuid4().hex}")
    assert _delete(client, target["id"])["status"] == "pass"
    working_store = service.vector_store
    service.vector_store = OutageVectorStore(working_store)
    try:
        verified = client.post(f"/api/v1/documents/{target['id']}/verify").json()
    finally:
        service.vector_store = working_store
    by_layer = {check["layer"]: check["status"] for check in verified["checks"]}
    assert by_layer["chromadb_api"] == "blocked"
    assert by_layer["application_retrieval"] == "blocked"
    assert verified["status"] == "blocked"
    with session_factory() as session:
        stored = session.query(DocumentRecord).filter_by(id=target["id"]).one()
        assert stored.content == ""
        assert stored.status == "deleted"


def test_repeated_verify_appends_history(app_environment) -> None:
    client, _, _ = app_environment
    target = _upload(client, f"ghost-erase-vrepeat-{uuid.uuid4().hex}")
    first = client.post(f"/api/v1/documents/{target['id']}/verify").json()
    second = client.post(f"/api/v1/documents/{target['id']}/verify").json()
    assert first["operation_id"] != second["operation_id"]
    assert first["kind"] == second["kind"] == "verification"
    for operation_id in (first["operation_id"], second["operation_id"]):
        assert client.get(f"/api/v1/deletion-operations/{operation_id}").status_code == 200


def test_verify_does_not_mask_deletion_history(app_environment) -> None:
    client, service, _ = app_environment
    target = _upload(client, f"ghost-erase-vmask-{uuid.uuid4().hex}")
    report = _delete(client, target["id"])
    assert report["status"] == "pass"
    client.post(f"/api/v1/documents/{target['id']}/verify").json()
    listed = client.get("/api/v1/documents").json()
    entry = next(item for item in listed if item["id"] == target["id"])
    assert entry["latest_deletion_status"] == "pass"
    assert entry["latest_deletion_operation_id"] == report["operation_id"]


def test_verify_persists_across_service_reconstruction(app_environment) -> None:
    client, service, session_factory = app_environment
    target = _upload(client, f"ghost-erase-vrestart-{uuid.uuid4().hex}")
    verified = client.post(f"/api/v1/documents/{target['id']}/verify").json()
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
        persisted = client.get(f"/api/v1/deletion-operations/{verified['operation_id']}")
        assert persisted.status_code == 200
        assert persisted.json()["kind"] == "verification"
        assert len(persisted.json()["checks"]) == 5
    finally:
        main.service = replacement


def test_duplicate_chunk_ids_do_not_touch_other_documents(app_environment) -> None:
    _, service, _ = app_environment
    target = service.create_document(f"Target {uuid.uuid4().hex}", filename="target.md")
    control = service.create_document(f"Control {uuid.uuid4().hex}", filename="control.txt")
    # Chroma itself rejects duplicated IDs; the failure must stay local:
    # nothing deleted anywhere, control untouched.
    with pytest.raises(Exception):
        service.vector_store.delete_chunks(target["id"], target["chunk_ids"] + target["chunk_ids"])
    assert sorted(service.vector_store.document_chunks(target["id"])["ids"]) == sorted(
        target["chunk_ids"]
    )
    assert service.vector_store.document_chunks(control["id"])["ids"] == control["chunk_ids"]


def test_cache_invalidation_is_scoped_to_one_document(app_environment) -> None:
    _, service, session_factory = app_environment
    cache = service.cache
    doc_a, doc_b = str(uuid.uuid4()), str(uuid.uuid4())
    with session_factory() as session:
        for document_id in (doc_a, doc_b):
            session.add(
                DocumentRecord(
                    id=document_id,
                    filename="scope.txt",
                    source="upload",
                    content="scope body",
                    content_hash="hash",
                    chunk_ids="[]",
                    source_file_path=None,
                    status="indexed",
                    created_at="2026-01-01T00:00:00+00:00",
                )
            )
        session.commit()
    cache.put("shared query", [{"document_id": doc_a, "chunk_id": "a1", "distance": 0.1}])
    cache.put("shared query", [{"document_id": doc_b, "chunk_id": "b1", "distance": 0.2}])
    assert cache.invalidate_document(doc_a)["invalidated_count"] == 1
    assert cache.residuals(doc_a)["residual_count"] == 0
    assert cache.residuals(doc_b)["residual_count"] == 1
    assert [entry["document_id"] for entry in cache.get("shared query")] == [doc_b]


def test_verify_query_outage_is_blocked_not_passed(app_environment) -> None:
    client, service, session_factory = app_environment
    marker = f"ghost-erase-vqueryout-{uuid.uuid4().hex}"
    target = _upload(client, marker)
    working_store = service.vector_store
    service.vector_store = QueryOutageVectorStore(working_store)
    try:
        verified = client.post(f"/api/v1/documents/{target['id']}/verify").json()
    finally:
        service.vector_store = working_store
    assert verified["kind"] == "verification"
    assert verified["status"] == "fail"
    assert next(
        check for check in verified["checks"] if check["layer"] == "application_retrieval"
    )["status"] == "blocked"
    assert marker not in json.dumps(verified)
    with session_factory() as session:
        stored = session.query(DocumentRecord).filter_by(id=target["id"]).one()
        assert marker in stored.content
        assert stored.status == "indexed"
