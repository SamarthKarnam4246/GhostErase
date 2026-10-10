from __future__ import annotations

from typing import Any

from backend.app.integrations.telemetry import neatlogs_status, telemetry_span


_EVENT_SPANS = {
    "document_indexed": "document.ingestion",
    "deletion_operation_completed": "deletion.orchestration",
    "deletion_check_completed": "deletion.verification_check",
}


def emit_trace(event_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    span_name = _EVENT_SPANS.get(event_name, "ghosterase")
    safe_payload = {
        key: value
        for key, value in payload.items()
        if key in {
            "operation_id",
            "document_id",
            "operation_type",
            "chunk_count",
            "result_status",
            "check_layer",
            "result_count",
            "top_k",
            "model_identifier",
        }
    }
    with telemetry_span(span_name, kind="CHAIN", **safe_payload):
        pass
    return neatlogs_status()
