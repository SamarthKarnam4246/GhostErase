from __future__ import annotations

import json
import os
import stat
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import main
from backend.app.db.session import Base
from backend.app.models.deletion import DeletionCheck, DeletionOperation
from backend.app.models.document import DocumentRecord
from backend.app.services.document_service import DocumentService
from backend.app.services.vector_store import ChromaVectorStore

pytestmark = pytest.mark.integration


def _client_for_peer(app, peer_address: str) -> TestClient:
    async def peer_app(scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "client": (peer_address, 50000)}
        await app(scope, receive, send)

    return TestClient(peer_app)


def _response_keys(value) -> set[str]:
    if isinstance(value, dict):
        return {str(key).lower() for key in value} | set().union(
            *(_response_keys(child) for child in value.values())
        )
    if isinstance(value, list):
        return set().union(*(_response_keys(child) for child in value)) if value else set()
    return set()


class FailedDeleteVectorStore:
    def __init__(self, delegate: ChromaVectorStore) -> None:
        self.delegate = delegate

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    def delete_chunks(self, document_id: str, chunk_ids: list[str]) -> dict:
        raise RuntimeError("synthetic deletion failure")


class FailedAddVectorStore:
    def __init__(self, delegate: ChromaVectorStore, message: str) -> None:
        self.delegate = delegate
        self.message = message

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    def add_document(self, *_args, **_kwargs):
        raise RuntimeError(self.message)


@pytest.fixture
def app_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_path = tmp_path / "metadata.sqlite3"
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    collection_name = f"test-{uuid.uuid4().hex}"
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


def test_authorized_deletion_records_each_check_and_preserves_control(
    app_environment,
) -> None:
    client, service, session_factory = app_environment
    marker = f"ghost-erase-delete-{uuid.uuid4().hex}"
    target_content = f"Unique marker {marker}. This synthetic test paragraph describes a confidential draft."
    control_content = f"Unrelated control {uuid.uuid4().hex}. This synthetic control remains indexed."

    uploaded = client.post(
        "/api/v1/documents/upload",
        files={"file": ("target.txt", target_content.encode(), "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    target = uploaded.json()
    stored_chunks = service.vector_store.document_chunks(target["id"])
    assert stored_chunks["ids"] == target["chunk_ids"]
    assert stored_chunks["metadatas"]
    assert all(metadata["document_id"] == target["id"] for metadata in stored_chunks["metadatas"])
    assert [metadata["chunk_index"] for metadata in stored_chunks["metadatas"]] == list(
        range(len(target["chunk_ids"]))
    )
    control = service.create_document(control_content, filename="control.md")

    before = client.post("/api/v1/search", json={"query": marker, "limit": 10})
    assert any(hit["document_id"] == target["id"] for hit in before.json()["results"])
    assert Path(service.upload_dir, target["id"], "target.txt").is_file()

    deletion = client.post(
        f"/api/v1/documents/{target['id']}/delete",
        headers={"X-Delete-Token": "test-delete-token"},
    )
    assert deletion.status_code == 200, deletion.text
    report = deletion.json()
    assert report["status"] == "pass"
    assert {check["layer"] for check in report["checks"]} == {
        "source_file",
        "chromadb_api",
        "application_retrieval",
        "sql_metadata",
    }
    assert all(check["status"] == "pass" for check in report["checks"])
    assert all(check["operation_id"] == report["operation_id"] for check in report["checks"])
    assert all(check["document_id"] == target["id"] for check in report["checks"])
    assert all(check["chunk_ids"] == target["chunk_ids"] for check in report["checks"])
    assert report["scope"]["api_level_accessibility_only"] is True
    assert report["scope"]["physical_erasure_verified"] is False
    verified_report = client.post(f"/api/v1/documents/{target['id']}/verify").json()
    assert verified_report["operation_id"] == report["operation_id"]
    repeated_deletion = client.post(
        f"/api/v1/documents/{target['id']}/delete",
        headers={"X-Delete-Token": "test-delete-token"},
    ).json()
    assert repeated_deletion["operation_id"] == report["operation_id"]
    assert repeated_deletion["status"] == "pass"
    assert not Path(service.upload_dir, target["id"], "target.txt").exists()

    after = client.post("/api/v1/search", json={"query": marker, "limit": 10})
    assert all(hit["document_id"] != target["id"] for hit in after.json()["results"])
    control_after = client.post("/api/v1/search", json={"query": control_content, "limit": 10})
    assert any(hit["document_id"] == control["id"] for hit in control_after.json()["results"])

    restarted_vector_store = ChromaVectorStore(
        service.vector_store.path,
        collection_name=service.vector_store.collection.name,
    )
    assert restarted_vector_store.document_chunks(target["id"])["ids"] == []
    assert restarted_vector_store.document_chunks(control["id"])["ids"]

    restarted_service = DocumentService(
        session_factory=session_factory,
        vector_store=restarted_vector_store,
        upload_dir=service.upload_dir,
        delete_auth_token="test-delete-token",
    )
    monkeypatch_service = main.service
    main.service = restarted_service
    try:
        persisted_report = client.get(
            f"/api/v1/deletion-operations/{report['operation_id']}"
        )
        assert persisted_report.status_code == 200
        assert persisted_report.json()["status"] == "pass"
        assert restarted_service.list_documents()
        assert restarted_service.search(control_content, 10)
    finally:
        main.service = monkeypatch_service

    with session_factory() as session:
        stored_document = session.query(DocumentRecord).filter_by(id=target["id"]).one()
        operation = session.query(DeletionOperation).filter_by(id=report["operation_id"]).one()
        checks = session.query(DeletionCheck).filter_by(operation_id=operation.id).all()
        assert stored_document.content == ""
        assert operation.chunk_ids == json.dumps(target["chunk_ids"])
        assert len(checks) == 5
        assert marker not in operation.chunk_ids
        assert marker not in " ".join(check.actual_result for check in checks)
        assert session.query(DeletionOperation).filter_by(document_id=control["id"]).count() == 0


def test_failed_vector_delete_is_persisted_and_never_reports_success(
    app_environment,
) -> None:
    client, service, session_factory = app_environment
    marker = f"ghost-erase-failure-{uuid.uuid4().hex}"
    content = f"Unique marker {marker}. Keep this synthetic document retrievable for the failure test."
    document = service.create_document(content, filename="failure.txt")
    working_vector_store = service.vector_store
    service.vector_store = FailedDeleteVectorStore(working_vector_store)

    before = client.post("/api/v1/search", json={"query": marker, "limit": 10})
    assert any(hit["document_id"] == document["id"] for hit in before.json()["results"])

    response = client.post(
        f"/api/v1/documents/{document['id']}/delete",
        headers={"X-Delete-Token": "test-delete-token"},
    )
    report = response.json()
    assert response.status_code == 200
    assert report["status"] == "fail"
    assert any(check["status"] == "fail" for check in report["checks"])
    delete_check = next(check for check in report["checks"] if "collection.delete" in check["method"])
    residual_check = next(
        check for check in report["checks"] if check["method"] == "collection.get(where=document_id)"
    )
    retrieval_check = next(check for check in report["checks"] if check["layer"] == "application_retrieval")
    assert delete_check["status"] == "fail"
    assert residual_check["status"] == "fail"
    assert retrieval_check["status"] == "fail"
    assert marker not in json.dumps(report)
    assert not Path(service.upload_dir, document["id"], "failure.txt").exists()
    dashboard_counts = client.get("/api/v1/dashboard").json()["counts"]
    assert dashboard_counts["indexed_documents"] == 1
    assert dashboard_counts["deletion_operations_by_status"] == {"fail": 1}
    assert dashboard_counts["checks"] == {"pass": 1, "fail": 3, "blocked": 1, "not_verified": 0}

    after = client.post("/api/v1/search", json={"query": marker, "limit": 10})
    assert any(hit["document_id"] == document["id"] for hit in after.json()["results"])
    with session_factory() as session:
        stored_document = session.query(DocumentRecord).filter_by(id=document["id"]).one()
        stored_operation = session.query(DeletionOperation).filter_by(id=report["operation_id"]).one()
        assert stored_document.status == "deletion_failed"
        assert stored_document.content == content
        assert stored_operation.status == "fail"

    service.vector_store = working_vector_store
    retried = client.post(
        f"/api/v1/documents/{document['id']}/delete",
        headers={"X-Delete-Token": "test-delete-token"},
    ).json()
    assert retried["status"] == "pass"
    source_check = next(check for check in retried["checks"] if check["layer"] == "source_file")
    assert source_check["actual_result"]["removal_previously_completed"] is True
    with session_factory() as session:
        assert session.query(DocumentRecord).filter_by(id=document["id"]).one().content == ""


def test_deletion_requires_configured_authorization_and_known_document(app_environment) -> None:
    client, service, session_factory = app_environment
    document = service.create_document(f"Auth check {uuid.uuid4().hex}", filename="auth.txt")

    unauthorized = client.post(f"/api/v1/documents/{document['id']}/delete")
    assert unauthorized.status_code == 403
    unknown = client.post(
        f"/api/v1/documents/{uuid.uuid4()}/delete",
        headers={"X-Delete-Token": "test-delete-token"},
    )
    assert unknown.json()["status"] == "not_verified"
    with session_factory() as session:
        assert session.query(DeletionOperation).count() == 0


def test_local_only_request_policy_blocks_remote_clients_and_untrusted_origins(app_environment) -> None:
    remote_client = _client_for_peer(main.app, "192.0.2.10")
    try:
        assert remote_client.get("/api/v1/health").status_code == 403
    finally:
        remote_client.close()

    with _client_for_peer(main.app, "127.0.0.1") as client:
        rejected_origin = client.post(
            "/api/v1/integrations/cfo/connect",
            headers={"Origin": "https://attacker.example"},
        )
        assert rejected_origin.status_code == 403
        allowed_local_origin = client.get(
            "/api/v1/health",
            headers={"Origin": "http://localhost:5173"},
        )
        assert allowed_local_origin.status_code == 200
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert response.headers["Cache-Control"] == "no-store"
        for path in (
            "/api/v1/health",
            "/api/v1/integrations/neatlogs",
            "/api/v1/integrations/cfo",
            "/api/v1/dashboard",
        ):
            response_keys = _response_keys(client.get(path).json())
            assert not response_keys.intersection(
                {
                    "api_key",
                    "access_token",
                    "refresh_token",
                    "client_secret",
                    "delete_auth_token",
                }
            )


def test_upload_creation_uses_restrictive_posix_permissions(app_environment) -> None:
    _, service, _ = app_environment
    document = service.create_document(f"Permissions {uuid.uuid4().hex}", filename="private.txt")
    source = Path(service.upload_dir, document["id"], "private.txt")
    assert source.is_file()
    if os.name != "nt":
        assert stat.S_IMODE(source.stat().st_mode) & 0o077 == 0
        assert stat.S_IMODE(source.parent.stat().st_mode) & 0o077 == 0
        assert stat.S_IMODE(service.upload_dir.stat().st_mode) & 0o077 == 0


def test_dashboard_counts_and_document_status_come_from_persisted_records(app_environment) -> None:
    client, service, _ = app_environment
    marker = f"dashboard-synthetic-{uuid.uuid4().hex}"
    document = service.create_document(f"Private document content {marker}", filename="dashboard.txt")

    listed = client.get("/api/v1/documents")
    assert listed.status_code == 200
    document_entry = next(item for item in listed.json() if item["id"] == document["id"])
    assert document_entry["created_at"]
    assert document_entry["status"] == "indexed"
    assert document_entry["latest_deletion_status"] is None

    dashboard = client.get("/api/v1/dashboard")
    assert dashboard.status_code == 200, dashboard.text
    initial = dashboard.json()
    assert initial["counts"]["indexed_documents"] == 1
    assert initial["counts"]["deletion_operations"] == 0
    assert initial["counts"]["checks"] == {
        "pass": 0,
        "fail": 0,
        "blocked": 0,
        "not_verified": 0,
    }
    assert initial["integrations"]["neatlogs_developer_mcp"]["state"] == "not_configured"
    assert initial["integrations"]["entire_copilot_cli"]["verified"] is False
    assert marker not in json.dumps(initial)

    deletion = client.post(
        f"/api/v1/documents/{document['id']}/delete",
        headers={"X-Delete-Token": "test-delete-token"},
    )
    assert deletion.status_code == 200
    operation_id = deletion.json()["operation_id"]

    updated = client.get("/api/v1/dashboard").json()
    assert updated["counts"]["indexed_documents"] == 0
    assert updated["counts"]["deletion_operations"] == 1
    assert updated["counts"]["deletion_operations_by_status"] == {"pass": 1}
    assert updated["counts"]["checks"] == {"pass": 5, "fail": 0, "blocked": 0, "not_verified": 0}
    assert updated["recent_operations"][0]["operation_id"] == operation_id

    after_list = client.get("/api/v1/documents").json()
    document_after = next(item for item in after_list if item["id"] == document["id"])
    assert document_after["status"] == "deleted"
    assert document_after["latest_deletion_status"] == "pass"
    assert document_after["latest_deletion_operation_id"] == operation_id
    operation_list = client.get("/api/v1/deletion-operations")
    assert operation_list.status_code == 200
    assert operation_list.json()[0]["operation_id"] == operation_id


def test_upload_rejects_unsupported_types_invalid_utf8_and_oversize_files(app_environment) -> None:
    client, _, _ = app_environment
    unsupported = client.post(
        "/api/v1/documents/upload",
        files={"file": ("notes.pdf", b"not text", "application/pdf")},
    )
    invalid_type = client.post(
        "/api/v1/documents/upload",
        files={"file": ("notes.txt", b"not accepted", "application/octet-stream")},
    )
    invalid_utf8 = client.post(
        "/api/v1/documents/upload",
        files={"file": ("notes.txt", b"\xff", "text/plain")},
    )
    oversized = client.post(
        "/api/v1/documents/upload",
        files={"file": ("notes.txt", b"x" * (5 * 1024 * 1024 + 1), "text/plain")},
    )
    assert unsupported.status_code == 415
    assert invalid_type.status_code == 415
    assert invalid_utf8.status_code == 400
    assert oversized.status_code == 413


def test_indexing_failure_is_sanitized_and_does_not_leave_success_state(app_environment) -> None:
    client, service, session_factory = app_environment
    marker = f"synthetic-index-error-{uuid.uuid4().hex}"
    working_store = service.vector_store
    service.vector_store = FailedAddVectorStore(working_store, marker)
    response = client.post(
        "/api/v1/documents/upload",
        files={"file": ("fails.txt", f"Safe body {marker}".encode(), "text/plain")},
    )
    assert response.status_code == 503
    assert marker not in response.text
    assert "indexing failed" in response.json()["detail"]
    with session_factory() as session:
        assert session.query(DocumentRecord).count() == 0
    assert not list(service.upload_dir.glob("*"))


def test_chromadb_query_failure_returns_error_instead_of_empty_success(app_environment) -> None:
    client, service, _ = app_environment
    marker = f"synthetic-chroma-error-{uuid.uuid4().hex}"
    service.vector_store.query = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(marker))
    response = client.post("/api/v1/search", json={"query": "synthetic", "limit": 5})
    assert response.status_code == 503
    assert response.json()["detail"] == "ChromaDB retrieval failed."
    assert marker not in response.text


def test_local_app_does_not_configure_a_hosted_model_provider(app_environment) -> None:
    client, _, _ = app_environment
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert "ChromaDB DefaultEmbeddingFunction" in health.json()["embedding_provider"]
    assert "hosted" not in health.json()["embedding_provider"].lower()
    assert not _response_keys(health.json()).intersection({"api_key", "litellm_api_key"})


def test_delete_selection_cannot_include_another_documents_chunks(app_environment) -> None:
    _, service, _ = app_environment
    target = service.create_document(f"Target {uuid.uuid4().hex}", filename="target.md")
    control = service.create_document(f"Control {uuid.uuid4().hex}", filename="control.txt")
    control_ids_before = service.vector_store.document_chunks(control["id"])["ids"]

    with pytest.raises(RuntimeError, match="do not exactly match"):
        service.vector_store.delete_chunks(target["id"], target["chunk_ids"] + control_ids_before)

    assert service.vector_store.document_chunks(control["id"])["ids"] == control_ids_before


def test_source_path_cannot_select_another_documents_file(app_environment) -> None:
    client, service, session_factory = app_environment
    target = service.create_document(f"Target {uuid.uuid4().hex}", filename="target.txt")
    control = service.create_document(f"Control {uuid.uuid4().hex}", filename="control.txt")
    control_path = Path(service.upload_dir, control["id"], "control.txt")
    with session_factory() as session:
        target_record = session.query(DocumentRecord).filter_by(id=target["id"]).one()
        target_record.source_file_path = str(control_path.resolve())
        session.commit()

    response = client.post(
        f"/api/v1/documents/{target['id']}/delete",
        headers={"X-Delete-Token": "test-delete-token"},
    )

    report = response.json()
    source_check = next(check for check in report["checks"] if check["layer"] == "source_file")
    assert report["status"] == "fail"
    assert source_check["status"] == "fail"
    assert control_path.is_file()
    assert service.vector_store.document_chunks(control["id"])["ids"] == control["chunk_ids"]
