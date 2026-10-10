from __future__ import annotations

import json
import sys
import uuid
from types import SimpleNamespace

import pytest
from neatlogs.core.masking_exporter import MaskingSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Status, StatusCode

from backend.app.integrations import telemetry
from backend.app.integrations.telemetry import redact_telemetry

pytestmark = pytest.mark.unit


class CapturingExporter(SpanExporter):
    def __init__(self) -> None:
        self.spans = []

    def export(self, spans):
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


def test_official_export_boundary_removes_content_and_keeps_approved_metadata() -> None:
    marker = f"ghost-erase-telemetry-{uuid.uuid4().hex}"
    capture = CapturingExporter()
    guarded_exporter = MaskingSpanExporter(capture, redact_telemetry)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(guarded_exporter))
    span = provider.get_tracer("ghosterase-tests").start_span(
        "document.ingestion",
        attributes={
            "document_id": str(uuid.uuid4()),
            "chunk_count": 2,
            "input.value": marker,
            "query": marker,
            "authorization": f"Bearer {marker}",
        },
    )
    span.add_event("document.content", {"content": marker})
    span.set_status(Status(StatusCode.ERROR, marker))
    span.end()

    assert len(capture.spans) == 1
    exported = capture.spans[0]
    serialized = json.dumps(
        {
            "name": exported.name,
            "attributes": dict(exported.attributes or {}),
            "events": [
                {"name": event.name, "attributes": dict(event.attributes or {})}
                for event in exported.events
            ],
            "resource": dict(exported.resource.attributes or {}),
            "status": exported.status.description,
        }
    )
    assert marker not in serialized
    assert exported.attributes["chunk_count"] == 2
    assert exported.attributes["document_id"]
    assert exported.events == ()
    assert exported.status.description is None
    provider.shutdown()


def test_redactor_drops_logs_unknown_fields_and_unapproved_identifiers() -> None:
    marker = f"ghost-erase-marker-{uuid.uuid4().hex}"
    span = redact_telemetry(
        {
            "signal": "span",
            "name": marker,
            "attributes": {
                "document_id": marker,
                "model_identifier": marker,
                "input.value": marker,
                "safe_count": 3,
            },
            "events": [{"name": marker, "attributes": {"content": marker}}],
            "links": [{"trace_id": marker}],
            "resource": {"attributes": {"document.content": marker}},
            "status": {"code": "ERROR", "description": marker},
        }
    )
    assert span is not None
    assert marker not in json.dumps(span)
    assert span["attributes"] == {}
    assert span["events"] == []
    assert span["links"] == []
    assert redact_telemetry({"signal": "log", "body": marker}) is None


def test_missing_neatlogs_credentials_are_explicitly_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telemetry.settings, "neatlogs_api_key", None)
    assert telemetry._initialization_status() is False
    status = telemetry.neatlogs_status()
    assert status["status"] == "blocked"
    assert status["verified"] is False
    assert "not configured" in status["detail"]


def test_neatlogs_initialization_failure_is_sanitized_and_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    marker = f"credential-{uuid.uuid4().hex}"
    fake_sdk = SimpleNamespace(init=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError(marker)))
    monkeypatch.setattr(telemetry.settings, "neatlogs_api_key", "synthetic-test-credential")
    monkeypatch.setattr(telemetry.settings, "neatlogs_endpoint", "https://ingest.neatlogs.com")
    monkeypatch.setattr(telemetry, "_INITIALIZED", False)
    monkeypatch.setattr(telemetry, "_INITIALIZATION_ERROR", None)
    monkeypatch.setitem(sys.modules, "neatlogs", fake_sdk)
    try:
        assert telemetry._initialization_status() is False
        status = telemetry.neatlogs_status()
        assert status["status"] == "blocked"
        assert "RuntimeError" in status["detail"]
        assert marker not in json.dumps(status)
        assert "synthetic-test-credential" not in json.dumps(status)
    finally:
        monkeypatch.setattr(telemetry, "_INITIALIZED", False)
        monkeypatch.setattr(telemetry, "_INITIALIZATION_ERROR", None)


def test_neatlogs_trace_start_failure_is_not_reported_as_unverified_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = f"export-failure-{uuid.uuid4().hex}"
    fake_sdk = SimpleNamespace(
        trace=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(marker))
    )
    monkeypatch.setattr(telemetry.settings, "neatlogs_api_key", "synthetic-test-credential")
    monkeypatch.setattr(telemetry.settings, "neatlogs_endpoint", "https://ingest.neatlogs.com")
    monkeypatch.setattr(telemetry, "_INITIALIZED", True)
    monkeypatch.setattr(telemetry, "_INITIALIZATION_ERROR", None)
    monkeypatch.setitem(sys.modules, "neatlogs", fake_sdk)
    with telemetry.telemetry_span("document.ingestion", "WORKFLOW") as span:
        span.set_result("pass")
    status = telemetry.neatlogs_status()
    assert status["status"] == "blocked"
    assert "RuntimeError" in status["detail"]
    assert marker not in json.dumps(status)
    assert "synthetic-test-credential" not in json.dumps(status)
