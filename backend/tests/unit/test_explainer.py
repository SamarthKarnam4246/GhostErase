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
from backend.app.main import app as fastapi_app
from backend.app.models.deletion import DeletionCheck, DeletionOperation
from backend.app.services.document_service import DocumentService
from backend.app.services.explainer import ExplanationError, LiteLLMExplanationProvider
from backend.app.services.vector_store import ChromaVectorStore

pytestmark = pytest.mark.integration


class CapturingProvider:
    name = "test-capturing"

    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.seen: list[dict] = []

    def explain(self, evidence: dict) -> str:
        self.seen.append(evidence)
        return self.response_text


class RaisingProvider:
    name = "test-raising"

    def __init__(self, error: Exception) -> None:
        self.error = error

    def explain(self, evidence: dict) -> str:
        raise self.error


class FailingVectorStore:
    def __init__(self, delegate: ChromaVectorStore) -> None:
        self.delegate = delegate

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    def delete_chunks(self, document_id: str, chunk_ids: list[str]) -> dict:
        raise RuntimeError("synthetic deletion failure")


@pytest.fixture
def app_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_path = tmp_path / "metadata.sqlite3"
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    collection_name = f"explain-{uuid.uuid4().hex}"
    vector_store = ChromaVectorStore(tmp_path / "chroma", collection_name=collection_name)
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
        await fastapi_app(scope, receive, send)

    client = TestClient(peer_app)
    yield client, service, session_factory
    client.close()
    engine.dispose()


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


def _stored_status(session_factory, operation_id: str) -> tuple[str, list[str]]:
    with session_factory() as session:
        operation = session.query(DeletionOperation).filter_by(id=operation_id).one()
        checks = session.query(DeletionCheck).filter_by(operation_id=operation_id).all()
        return operation.status, sorted(check.status for check in checks)


def test_unconfigured_provider_returns_unavailable_and_keeps_report_working(
    app_environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.core.config import settings

    # Hermetic precondition: no provider key, regardless of local .env.
    monkeypatch.setattr(settings, "litellm_api_key", None)
    monkeypatch.setattr(settings, "groq_api_key", None)
    client, service, session_factory = app_environment
    assert service.explainer_provider is None
    target = _upload(client, f"ghost-erase-expunavail-{uuid.uuid4().hex}")
    report = _delete(client, target["id"])
    assert report["status"] == "pass"

    explained = client.post(f"/api/v1/deletion-operations/{report['operation_id']}/explain")
    assert explained.status_code == 200
    payload = explained.json()
    assert payload["outcome"] == "unavailable"
    assert payload["provider"] is None
    assert payload["verified_status"] == "pass"
    assert payload["suggested_steps"] == []
    assert payload["advisory_only"] is True
    assert _stored_status(session_factory, report["operation_id"]) == ("pass", ["pass"] * 6)
    assert client.get(f"/api/v1/deletion-operations/{report['operation_id']}").json()["status"] == "pass"


def test_malformed_output_is_degraded(app_environment) -> None:
    client, service, _ = app_environment
    target = _upload(client, f"ghost-erase-expmal-{uuid.uuid4().hex}")
    report = _delete(client, target["id"])
    service.explainer_provider = CapturingProvider("this is not json {{{")
    try:
        payload = client.post(
            f"/api/v1/deletion-operations/{report['operation_id']}/explain"
        ).json()
    finally:
        service.explainer_provider = None
    assert payload["outcome"] == "degraded"
    assert payload["verified_status"] == "pass"
    assert payload["suggested_steps"] == []
    assert payload["detail"] is not None


def test_non_dict_json_is_degraded(app_environment) -> None:
    client, service, _ = app_environment
    target = _upload(client, f"ghost-erase-expJist-{uuid.uuid4().hex}")
    report = _delete(client, target["id"])
    service.explainer_provider = CapturingProvider("[1, 2, 3]")
    try:
        payload = client.post(
            f"/api/v1/deletion-operations/{report['operation_id']}/explain"
        ).json()
    finally:
        service.explainer_provider = None
    assert payload["outcome"] == "degraded"
    assert payload["verified_status"] == report["status"]


def test_unsafe_and_unknown_recommendations_are_dropped(app_environment) -> None:
    client, service, session_factory = app_environment
    target = _upload(client, f"ghost-erase-expunsafe-{uuid.uuid4().hex}")
    working_store = service.vector_store
    service.vector_store = FailingVectorStore(working_store)
    try:
        report = _delete(client, target["id"])
    finally:
        service.vector_store = working_store
    assert report["status"] == "fail"
    real_check_id = report["checks"][0]["check_id"]
    service.explainer_provider = CapturingProvider(
        json.dumps(
            {
                "status": "pass",
                "verdict": "success",
                "summary": "Claimed success.",
                "likely_causes": ["cause one"],
                "suggested_steps": [
                    {"action": "inspect_check", "detail": "Look at the check.", "check_id": real_check_id},
                    {"action": "delete_document", "detail": "Delete it again right now."},
                    {"action": "inspect_check", "detail": "Unknown check.", "check_id": "no-such-check"},
                    {"action": "retry_operation_via_api", "detail": "   "},
                    "not-a-step",
                ],
            }
        )
    )
    try:
        payload = client.post(
            f"/api/v1/deletion-operations/{report['operation_id']}/explain"
        ).json()
    finally:
        service.explainer_provider = None
    # The model claimed success; the deterministic status must win.
    assert payload["outcome"] == "ok"
    assert payload["verified_status"] == "fail"
    assert payload["summary"] == "Claimed success."
    assert len(payload["suggested_steps"]) == 1
    assert payload["suggested_steps"][0]["action"] == "inspect_check"
    assert payload["suggested_steps"][0]["check_id"] == real_check_id
    assert payload["dropped_unsafe_steps"] == 4
    assert "status" not in payload or payload.get("verified_status") == "fail"
    assert _stored_status(session_factory, report["operation_id"])[0] == "fail"


def test_provider_errors_are_degraded_without_touching_status(app_environment) -> None:
    client, service, session_factory = app_environment
    target = _upload(client, f"ghost-erase-experr-{uuid.uuid4().hex}")
    report = _delete(client, target["id"])
    for error in (TimeoutError("synthetic timeout"), ExplanationError("ModelRefused")):
        service.explainer_provider = RaisingProvider(error)
        try:
            payload = client.post(
                f"/api/v1/deletion-operations/{report['operation_id']}/explain"
            ).json()
        finally:
            service.explainer_provider = None
        assert payload["outcome"] == "degraded"
        assert payload["verified_status"] == "pass"
        assert payload["suggested_steps"] == []
    assert _stored_status(session_factory, report["operation_id"]) == ("pass", ["pass"] * 6)


def test_no_ai_response_can_change_deletion_status(
    app_environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.core.config import settings

    # Hermetic precondition: no provider key, regardless of local .env.
    monkeypatch.setattr(settings, "litellm_api_key", None)
    monkeypatch.setattr(settings, "groq_api_key", None)
    client, service, session_factory = app_environment
    target = _upload(client, f"ghost-erase-expinvariant-{uuid.uuid4().hex}")
    working_store = service.vector_store
    service.vector_store = FailingVectorStore(working_store)
    try:
        report = _delete(client, target["id"])
    finally:
        service.vector_store = working_store
    assert report["status"] == "fail"
    before = _stored_status(session_factory, report["operation_id"])
    unavailable = client.post(
        f"/api/v1/deletion-operations/{report['operation_id']}/explain"
    ).json()
    assert unavailable["outcome"] == "unavailable"
    assert unavailable["verified_status"] == "fail"
    payloads = [unavailable]
    service.explainer_provider = CapturingProvider(
        json.dumps({"status": "pass", "suggested_steps": [{"action": "escalate_to_operator", "detail": "Ask."}]})
    )
    try:
        payloads.append(
            client.post(f"/api/v1/deletion-operations/{report['operation_id']}/explain").json()
        )
    finally:
        service.explainer_provider = None
    service.explainer_provider = CapturingProvider("garbage{{{")
    try:
        payloads.append(
            client.post(f"/api/v1/deletion-operations/{report['operation_id']}/explain").json()
        )
    finally:
        service.explainer_provider = None
    for payload in payloads:
        assert payload["verified_status"] == "fail"
    assert _stored_status(session_factory, report["operation_id"]) == before


def test_evidence_sent_to_provider_contains_no_document_text_or_secrets(
    app_environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, service, _ = app_environment
    marker = f"ghost-erase-exppriv-{uuid.uuid4().hex}"
    target = _upload(client, marker)
    report = _delete(client, target["id"])
    monkeypatch.setattr(service, "delete_auth_token", "secret-token-xyz")
    provider = CapturingProvider("{}")
    service.explainer_provider = provider
    try:
        payload = client.post(
            f"/api/v1/deletion-operations/{report['operation_id']}/explain"
        ).json()
    finally:
        service.explainer_provider = None
    assert payload["outcome"] == "ok"
    assert len(provider.seen) == 1
    serialized = json.dumps(provider.seen[0])
    assert marker not in serialized
    assert "secret-token-xyz" not in serialized
    assert "synthetic body" not in serialized
    assert set(provider.seen[0]) == {
        "operation_id",
        "document_id",
        "verified_status",
        "check_count",
        "status_counts",
        "checks",
    }
    for check in provider.seen[0]["checks"]:
        assert set(check) == {
            "check_id",
            "layer",
            "method",
            "status",
            "error_category",
            "residual_count",
        }


def test_explain_unknown_operation_is_404(app_environment) -> None:
    client, _, _ = app_environment
    response = client.post(f"/api/v1/deletion-operations/{uuid.uuid4()}/explain")
    assert response.status_code == 404


def test_fenced_json_output_is_accepted(app_environment) -> None:
    client, service, _ = app_environment
    target = _upload(client, f"ghost-erase-expfence-{uuid.uuid4().hex}")
    report = _delete(client, target["id"])
    service.explainer_provider = CapturingProvider(
        '```json\n{"summary": "Fenced summary.", "suggested_steps": []}\n```'
    )
    try:
        payload = client.post(
            f"/api/v1/deletion-operations/{report['operation_id']}/explain"
        ).json()
    finally:
        service.explainer_provider = None
    assert payload["outcome"] == "ok"
    assert payload["summary"] == "Fenced summary."
    assert payload["verified_status"] == report["status"]


def test_litellm_provider_has_bounded_defaults() -> None:
    provider = LiteLLMExplanationProvider()
    assert provider.name == "litellm"
    assert provider.timeout_seconds == 20
    assert provider.model


def test_provider_credentials_selected_by_model_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "litellm_api_key", "litellm-key")
    monkeypatch.setattr(settings, "litellm_api_base", "https://litellm.example/v1")
    monkeypatch.setattr(settings, "groq_api_key", "groq-key")
    assert LiteLLMExplanationProvider._credentials_for("openai/claude-haiku") == (
        "litellm-key",
        "https://litellm.example/v1",
    )
    assert LiteLLMExplanationProvider._credentials_for("groq/some-model") == ("groq-key", None)


def test_missing_provider_key_is_unavailable_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "groq_api_key", None)
    provider = LiteLLMExplanationProvider("groq/some-model")
    with pytest.raises(ExplanationError) as error:
        provider.explain({"operation_id": "op-1"})
    assert error.value.category == "ProviderUnavailable"
