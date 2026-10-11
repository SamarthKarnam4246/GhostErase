from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.app import main
from backend.app.db.session import Base, ensure_schema
from backend.app.models.deletion import DeletionCheck, DeletionOperation
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
def failure_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_path = tmp_path / "metadata.sqlite3"
    sqlite3.connect(str(database_path)).close()
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    vector_store = ChromaVectorStore(
        tmp_path / "chroma", collection_name=f"storage-fail-{uuid.uuid4().hex}"
    )
    service = DocumentService(
        session_factory=session_factory,
        vector_store=vector_store,
        upload_dir=tmp_path / "uploads",
        delete_auth_token="test-delete-token",
    )
    monkeypatch.setattr(main, "service", service)
    yield service, session_factory, database_path, engine
    engine.dispose()


def test_locked_metadata_store_returns_not_verified_without_recording(
    failure_environment,
) -> None:
    service, session_factory, database_path, engine = failure_environment
    marker = f"ghost-erase-locked-{uuid.uuid4().hex}"
    content = f"Locked-store marker {marker}. Synthetic body."
    document = service.create_document(content, filename="locked.txt")

    @event.listens_for(engine, "connect")
    def _fail_fast_on_lock(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA busy_timeout=0")

    engine.dispose()

    holder = sqlite3.connect(str(database_path), isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        client = _client_for_peer("127.0.0.1")
        try:
            response = client.post(
                f"/api/v1/documents/{document['id']}/delete",
                headers={"X-Delete-Token": "test-delete-token"},
            )
        finally:
            client.close()
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "not_verified"
    assert "operation_id" not in body
    assert marker not in response.text
    with session_factory() as session:
        assert session.query(DeletionOperation).filter_by(document_id=document["id"]).count() == 0
        stored = session.query(DocumentRecord).filter_by(id=document["id"]).one()
        assert stored.content == content
        assert stored.status == "indexed"


def test_missing_source_file_is_recorded_as_fail_not_success(
    failure_environment,
) -> None:
    service, session_factory, _, _ = failure_environment
    marker = f"ghost-erase-missingsrc-{uuid.uuid4().hex}"
    content = f"Missing-source marker {marker}. Synthetic body."
    document = service.create_document(content, filename="gone.txt")
    Path(service.upload_dir, document["id"], "gone.txt").unlink()

    client = _client_for_peer("127.0.0.1")
    try:
        response = client.post(
            f"/api/v1/documents/{document['id']}/delete",
            headers={"X-Delete-Token": "test-delete-token"},
        )
    finally:
        client.close()
    assert response.status_code == 200
    report = response.json()
    assert report["status"] == "fail"
    source_check = next(check for check in report["checks"] if check["layer"] == "source_file")
    assert source_check["status"] == "fail"
    assert source_check["actual_result"].get("file_existed_before_delete") is False
    assert marker not in response.text
    with session_factory() as session:
        stored = session.query(DocumentRecord).filter_by(id=document["id"]).one()
        assert stored.status == "deletion_failed"
        assert stored.content == content


def test_report_write_failure_preserves_earlier_evidence(
    failure_environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.orm import Session as OrmSession

    service, session_factory, _, _ = failure_environment
    marker = f"ghost-erase-reportfail-{uuid.uuid4().hex}"
    content = f"Report-failure marker {marker}. Synthetic body."
    document = service.create_document(content, filename="reportfail.txt")
    real_commit = OrmSession.commit

    def flaky_commit(self, *args, **kwargs):
        pending = [
            record
            for record in self.new
            if isinstance(record, DeletionCheck)
            and record.layer == "chromadb_api"
            and "collection.delete" in record.method
        ]
        if pending:
            raise RuntimeError("synthetic report write failure")
        return real_commit(self, *args, **kwargs)

    monkeypatch.setattr(OrmSession, "commit", flaky_commit)
    client = _client_for_peer("127.0.0.1")
    try:
        with pytest.raises(RuntimeError, match="synthetic report write failure"):
            client.post(
                f"/api/v1/documents/{document['id']}/delete",
                headers={"X-Delete-Token": "test-delete-token"},
            )
    finally:
        client.close()

    with session_factory() as session:
        operation = session.query(DeletionOperation).filter_by(document_id=document["id"]).one()
        assert operation.status == "running"
        checks = (
            session.query(DeletionCheck)
            .filter_by(operation_id=operation.id)
            .order_by(DeletionCheck.checked_at)
            .all()
        )
        assert [(check.layer, check.status) for check in checks] == [("source_file", "pass")]
        assert marker not in json.dumps([check.actual_result for check in checks])
        assert session.query(DocumentRecord).filter_by(id=document["id"]).one().content == content


def test_concurrent_deletes_do_not_corrupt_state(failure_environment) -> None:
    service, session_factory, _, _ = failure_environment
    marker = f"ghost-erase-race-{uuid.uuid4().hex}"
    content = f"Race marker {marker}. Synthetic body for concurrent deletion."
    document = service.create_document(content, filename="race.txt")
    barrier = threading.Barrier(2)
    outcomes: dict[int, dict] = {}

    def worker(index: int) -> None:
        client = _client_for_peer("127.0.0.1")
        try:
            barrier.wait(timeout=30)
            try:
                response = client.post(
                    f"/api/v1/documents/{document['id']}/delete",
                    headers={"X-Delete-Token": "test-delete-token"},
                )
                outcomes[index] = {"status_code": response.status_code, "body": response.json()}
            except Exception as error:
                outcomes[index] = {"status_code": -1, "body": {"error": type(error).__name__}}
        finally:
            client.close()

    threads = [threading.Thread(target=worker, args=(index,)) for index in (0, 1)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)
    assert not any(thread.is_alive() for thread in threads)
    assert set(outcomes) == {0, 1}

    bodies = [outcomes[index]["body"] for index in (0, 1)]
    assert [outcomes[index]["status_code"] for index in (0, 1)] == [200, 200]
    operation_ids = [body["operation_id"] for body in bodies]
    assert len(set(operation_ids)) == 2
    statuses = {body["status"] for body in bodies}
    assert statuses <= {"pass", "fail"}
    assert "pass" in statuses
    assert marker not in json.dumps(bodies)
    with session_factory() as session:
        assert session.query(DeletionOperation).filter_by(document_id=document["id"]).count() == 2
        stored = session.query(DocumentRecord).filter_by(id=document["id"]).one()
        assert stored.content == ""
        assert stored.status == "deleted"


def test_null_chunk_ids_legacy_row_does_not_crash_reads_or_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "legacy-null.sqlite3"
    marker = f"ghost-erase-nullchunks-{uuid.uuid4().hex}"
    raw = sqlite3.connect(str(database_path))
    raw.execute(
        "CREATE TABLE documents (id VARCHAR PRIMARY KEY, filename VARCHAR,"
        " source VARCHAR NOT NULL, content TEXT, content_hash VARCHAR NOT NULL,"
        " chunk_ids TEXT, source_file_path TEXT, status VARCHAR,"
        " created_at VARCHAR, deleted_at VARCHAR)"
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
        "INSERT INTO documents VALUES ('legacy-null', 'legacy.txt', 'upload',"
        f" 'Legacy body {marker}', 'deadbeef', NULL, NULL, 'indexed',"
        " '2026-01-01T00:00:00+00:00', NULL)"
    )
    raw.commit()
    raw.close()

    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    ensure_schema(engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    vector_store = ChromaVectorStore(
        tmp_path / "chroma", collection_name=f"null-chunks-{uuid.uuid4().hex}"
    )
    service = DocumentService(
        session_factory=session_factory,
        vector_store=vector_store,
        upload_dir=tmp_path / "uploads",
        delete_auth_token="test-delete-token",
    )
    monkeypatch.setattr(main, "service", service)
    client = _client_for_peer("127.0.0.1")
    try:
        listed = client.get("/api/v1/documents")
        assert listed.status_code == 200
        entry = next(item for item in listed.json() if item["id"] == "legacy-null")
        assert entry["chunk_count"] == 0

        searched = client.post("/api/v1/search", json={"query": "legacy", "limit": 5})
        assert searched.status_code == 200

        detail = client.get("/api/v1/documents/legacy-null/lineage")
        assert detail.status_code == 200
        assert detail.json()["document"]["chunk_count"] == 0

        report = client.post(
            "/api/v1/documents/legacy-null/delete",
            headers={"X-Delete-Token": "test-delete-token"},
        )
        assert report.status_code == 200
        body = report.json()
        # No recorded vector IDs exist, so vector deletion cannot succeed;
        # the report must say fail, never pass.
        assert body["status"] == "fail"
        assert marker not in report.text
        with session_factory() as session:
            stored = session.query(DocumentRecord).filter_by(id="legacy-null").one()
            assert stored.status == "deletion_failed"
            assert marker in stored.content
    finally:
        client.close()
    engine.dispose()


def test_concurrent_delete_and_verify_stay_well_formed(failure_environment) -> None:
    service, session_factory, _, _ = failure_environment
    marker = f"ghost-erase-racev-{uuid.uuid4().hex}"
    content = f"Race-verify marker {marker}. Synthetic body."
    document = service.create_document(content, filename="racev.txt")
    barrier = threading.Barrier(2)
    outcomes: dict[int, dict] = {}

    def delete_worker() -> None:
        client = _client_for_peer("127.0.0.1")
        try:
            barrier.wait(timeout=30)
            try:
                response = client.post(
                    f"/api/v1/documents/{document['id']}/delete",
                    headers={"X-Delete-Token": "test-delete-token"},
                )
                outcomes[0] = {"status_code": response.status_code, "body": response.json()}
            except Exception as error:
                outcomes[0] = {"status_code": -1, "body": {"error": type(error).__name__}}
        finally:
            client.close()

    def verify_worker() -> None:
        client = _client_for_peer("127.0.0.1")
        try:
            barrier.wait(timeout=30)
            try:
                response = client.post(f"/api/v1/documents/{document['id']}/verify")
                outcomes[1] = {"status_code": response.status_code, "body": response.json()}
            except Exception as error:
                outcomes[1] = {"status_code": -1, "body": {"error": type(error).__name__}}
        finally:
            client.close()

    threads = [
        threading.Thread(target=delete_worker),
        threading.Thread(target=verify_worker),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)
    assert not any(thread.is_alive() for thread in threads)
    assert set(outcomes) == {0, 1}
    bodies = [outcomes[0]["body"], outcomes[1]["body"]]
    assert [outcomes[0]["status_code"], outcomes[1]["status_code"]] == [200, 200]
    assert all("operation_id" in body and "status" in body for body in bodies)
    assert bodies[0]["status"] in {"pass", "fail"}
    assert bodies[1]["kind"] == "verification"
    assert marker not in json.dumps(bodies)
    with session_factory() as session:
        stored = session.query(DocumentRecord).filter_by(id=document["id"]).one()
        # Whatever the interleaving, the record is consistent: cleared content
        # always pairs with the terminal deleted state.
        assert (stored.content == "") == (stored.status == "deleted")
