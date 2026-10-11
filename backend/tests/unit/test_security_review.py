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
from backend.app.integrations import cfo_ai
from backend.app.services.document_service import DocumentService
from backend.app.services.vector_store import ChromaVectorStore

pytestmark = pytest.mark.integration

FORBIDDEN_RESPONSE_KEYS = {
    "api_key",
    "access_token",
    "refresh_token",
    "client_secret",
    "delete_auth_token",
}

FORBIDDEN_FRONTEND_STRINGS = (
    "DELETE_AUTH_TOKEN",
    "LITELLM_API_KEY",
    "NEATLOGS_API_KEY",
    "dangerouslySetInnerHTML",
    "sessionStorage",
)

# localStorage is allowed only for the namespaced UI-theme preference, never
# near secret-adjacent identifiers. The deletion token must stay memory-only.
THEME_STORAGE_KEY = "ghosterase-theme"
SECRET_ADJACENT_WORDS = ("token", "auth", "secret", "credential", "password", "api_key")


def _response_keys(value) -> set[str]:
    if isinstance(value, dict):
        return {str(key).lower() for key in value} | set().union(
            *(_response_keys(child) for child in value.values())
        )
    if isinstance(value, list):
        return set().union(*(_response_keys(child) for child in value)) if value else set()
    return set()


@pytest.fixture
def app_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_path = tmp_path / "metadata.sqlite3"
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    vector_store = ChromaVectorStore(
        tmp_path / "chroma", collection_name=f"sec-{uuid.uuid4().hex}"
    )
    service = DocumentService(
        session_factory=session_factory,
        vector_store=vector_store,
        upload_dir=tmp_path / "uploads",
        delete_auth_token="test-delete-token",
    )
    monkeypatch.setattr(main, "service", service)

    async def peer_app(scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "client": ("127.0.0.1", 50000)}
        await main.app(scope, receive, send)

    client = TestClient(peer_app)
    yield client, service
    client.close()
    engine.dispose()


def test_no_cors_middleware_and_trusted_host_present() -> None:
    middleware_names = [entry.cls.__name__ for entry in main.app.user_middleware]
    assert "CORSMiddleware" not in middleware_names
    assert "TrustedHostMiddleware" in middleware_names


def test_cfo_oauth_callback_binds_loopback_only() -> None:
    assert cfo_ai.CFO_CALLBACK_HOST == "127.0.0.1"
    assert cfo_ai.CFO_CALLBACK_PORT == 8765
    assert cfo_ai.CFO_REDIRECT_URI.startswith("http://127.0.0.1:8765/")


def test_sensitive_keys_absent_from_all_read_responses(app_environment) -> None:
    client, service = app_environment
    marker = f"ghost-erase-seckeys-{uuid.uuid4().hex}"
    uploaded = client.post(
        "/api/v1/documents/upload",
        files={"file": ("sec.txt", f"Security marker {marker} body.".encode(), "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    target = uploaded.json()
    client.post("/api/v1/search", json={"query": marker, "limit": 5})
    report = client.post(
        f"/api/v1/documents/{target['id']}/delete",
        headers={"X-Delete-Token": "test-delete-token"},
    ).json()
    assert report["status"] == "pass"

    bodies = [
        client.get("/api/v1/health").json(),
        client.get("/api/v1/integrations/neatlogs").json(),
        client.get("/api/v1/dashboard").json(),
        client.get("/api/v1/documents").json(),
        client.get("/api/v1/deletion-operations").json(),
        client.get(f"/api/v1/documents/{target['id']}/lineage").json(),
        client.get(f"/api/v1/deletion-operations/{report['operation_id']}").json(),
        client.post(f"/api/v1/deletion-operations/{report['operation_id']}/explain").json(),
        client.get("/api/v1/integrations/cfo").json(),
    ]
    for body in bodies:
        assert not _response_keys(body).intersection(FORBIDDEN_RESPONSE_KEYS)
    assert marker not in json.dumps(bodies)


def test_security_headers_present_on_data_routes(app_environment) -> None:
    client, service = app_environment
    target = client.post(
        "/api/v1/documents/upload",
        files={"file": ("hdr.txt", b"header check body", "text/plain")},
    ).json()
    report = client.post(
        f"/api/v1/documents/{target['id']}/delete",
        headers={"X-Delete-Token": "test-delete-token"},
    ).json()
    for response in (
        client.get(f"/api/v1/documents/{target['id']}/lineage"),
        client.get(f"/api/v1/deletion-operations/{report['operation_id']}"),
        client.post(f"/api/v1/deletion-operations/{report['operation_id']}/explain"),
    ):
        assert response.status_code == 200
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert response.headers["Cache-Control"] == "no-store"


def test_frontend_source_has_no_secrets_or_dangerous_sinks() -> None:
    frontend_src = Path(__file__).resolve().parents[3] / "frontend" / "src"
    assert frontend_src.is_dir()
    violations: list[str] = []
    for path in sorted(frontend_src.rglob("*")):
        if not path.is_file() or path.suffix not in {".ts", ".tsx", ".css", ".html"}:
            continue
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_FRONTEND_STRINGS:
            if forbidden in text:
                violations.append(f"{path.name}: {forbidden}")
        for lineno, line in enumerate(text.splitlines(), start=1):
            lowered = line.lower()
            if "localstorage" in lowered:
                if THEME_STORAGE_KEY not in line or any(
                    word in lowered for word in SECRET_ADJACENT_WORDS
                ):
                    violations.append(f"{path.name}:{lineno}: localStorage outside theme preference")
        if "eval(" in text:
            violations.append(f"{path.name}: eval(")
    assert violations == []


def test_untrusted_origin_is_rejected_on_mutating_routes(app_environment) -> None:
    client, _ = app_environment
    evil = {"Origin": "https://attacker.example"}
    assert (
        client.post(
            "/api/v1/documents/upload",
            files={"file": ("x.txt", b"csrf body", "text/plain")},
            headers=evil,
        ).status_code
        == 403
    )
    assert client.post("/api/v1/search", json={"query": "x", "limit": 5}, headers=evil).status_code == 403
    assert (
        client.post(
            f"/api/v1/documents/{uuid.uuid4()}/delete",
            headers={**evil, "X-Delete-Token": "test-delete-token"},
        ).status_code
        == 403
    )
    allowed = client.post(
        "/api/v1/documents/upload",
        files={"file": ("ok.txt", b"allowed origin body", "text/plain")},
        headers={"Origin": "http://localhost:5173"},
    )
    assert allowed.status_code == 200, allowed.text


def test_missing_token_reveals_nothing_about_document_existence(app_environment) -> None:
    client, service = app_environment
    target = service.create_document("Oracle probe body.", filename="oracle.txt")
    for document_id in (target["id"], str(uuid.uuid4())):
        response = client.post(f"/api/v1/documents/{document_id}/delete")
        assert response.status_code == 403
        assert response.json()["detail"] == "A configured deletion authorization token is required."


def test_empty_configured_token_stays_fail_closed(app_environment) -> None:
    from backend.app.services.document_service import DocumentService

    _, service = app_environment
    bare = DocumentService(
        session_factory=service.session_factory,
        vector_store=service.vector_store,
        upload_dir=service.upload_dir,
        delete_auth_token="",
    )
    assert bare.delete_document("anything", "anything")["status"] == "blocked"
    assert bare.delete_document("anything", None)["status"] == "blocked"


def test_unhandled_storage_errors_return_generic_500_without_content(app_environment) -> None:
    from sqlalchemy import text

    client, service = app_environment
    marker = f"ghost-erase-errleak-{uuid.uuid4().hex}"
    document = service.create_document(f"Leak probe {marker} body.", filename="leak.txt")
    with service.session_factory() as session:
        session.execute(text("DROP TABLE documents"))
        session.commit()

    async def quiet_peer_app(scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "client": ("127.0.0.1", 50000)}
        await main.app(scope, receive, send)

    quiet = TestClient(quiet_peer_app, raise_server_exceptions=False)
    try:
        response = quiet.get("/api/v1/documents")
    finally:
        quiet.close()
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert marker not in response.text
    assert document["id"] not in response.text
