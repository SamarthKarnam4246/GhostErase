from __future__ import annotations

import math
import re
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import urlsplit
from uuid import UUID

from backend.app.core.config import settings

_LOCK = threading.Lock()
_INITIALIZED = False
_INITIALIZATION_ERROR: str | None = None
_SAFE_ATTRIBUTES = {
    "operation_id",
    "document_id",
    "operation_type",
    "chunk_count",
    "result_status",
    "error_category",
    "check_layer",
    "result_count",
    "top_k",
    "model_identifier",
    "latency_ms",
    "neatlogs.span.kind",
    "neatlogs.auto_root",
    "openinference.span.kind",
}
_SAFE_SPAN_NAMES = {
    "document.ingestion",
    "document.retrieval",
    "rag.embedding",
    "rag.vector_insert",
    "rag.vector_query",
    "deletion.orchestration",
    "deletion.source_removal",
    "deletion.vector_removal",
    "deletion.residual_check",
    "deletion.post_delete_retrieval",
    "deletion.metadata_clear",
    "deletion.verification_check",
    "model.completion",
    "ghosterase",
}


def _initialization_status() -> bool:
    global _INITIALIZED, _INITIALIZATION_ERROR
    if not settings.neatlogs_api_key:
        return False
    with _LOCK:
        if _INITIALIZED:
            return True
        try:
            endpoint = urlsplit(settings.neatlogs_endpoint)
            hostname = (endpoint.hostname or "").lower()
            trusted_host = hostname == "neatlogs.com" or hostname.endswith(".neatlogs.com")
            if (
                endpoint.scheme != "https"
                or not trusted_host
                or endpoint.username is not None
                or endpoint.password is not None
                or endpoint.query
                or endpoint.fragment
            ):
                _INITIALIZATION_ERROR = "InsecureOrInvalidEndpoint"
                return False
            import neatlogs

            neatlogs.init(
                api_key=settings.neatlogs_api_key,
                endpoint=settings.neatlogs_endpoint,
                workflow_name="GhostErase",
                instrumentations=[],
                capture_logs=False,
                mask=redact_telemetry,
                register_shutdown_handlers=False,
            )
            _INITIALIZED = True
            _INITIALIZATION_ERROR = None
        except Exception as error:
            _INITIALIZATION_ERROR = type(error).__name__
    return _INITIALIZED


def _safe_value(key: str, value: Any) -> str | int | float | bool | None:
    if key not in _SAFE_ATTRIBUTES or isinstance(value, (dict, list, tuple, set)):
        return None
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        if key in {"operation_id", "document_id"}:
            try:
                return str(UUID(value)) if str(UUID(value)) == value.lower() else None
            except ValueError:
                return None
        if key == "result_status":
            return value if value in {"pass", "fail", "blocked", "not_verified"} else None
        if key == "operation_type":
            return value if value in {
                "document_ingestion",
                "document_index",
                "application_retrieval",
                "vector_retrieval",
                "deletion",
                "source_file_delete",
                "chromadb_delete",
                "chromadb_residual_check",
                "sql_content_clear",
                "model_connection_check",
                "grounded_generation",
                "hosted_embedding",
            } else None
        if key == "check_layer":
            return value if value in {
                "source_file",
                "chromadb_api",
                "application_retrieval",
                "sql_metadata",
            } else None
        if key == "error_category":
            return value if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value) else None
        if key == "model_identifier":
            if value == "all-MiniLM-L6-v2":
                return value
            return value[:128] if re.fullmatch(r"(openai|anthropic|amazon)/[A-Za-z0-9._-]{1,100}", value) else None
        if key in {"neatlogs.span.kind", "openinference.span.kind"}:
            return value if value in {"workflow", "chain", "tool", "retriever", "embedding", "llm"} else None
        return None
    return None


def redact_telemetry(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Export only allowlisted metadata; fail closed for logs and unknown signals."""
    if snapshot.get("signal") != "span":
        return None
    attributes = snapshot.get("attributes")
    safe_attributes = {}
    if isinstance(attributes, dict):
        for key, value in attributes.items():
            safe_value = _safe_value(key, value)
            if safe_value is not None:
                safe_attributes[key] = safe_value

    name = snapshot.get("name")
    safe_name = name if isinstance(name, str) and name in _SAFE_SPAN_NAMES else "ghosterase"
    resource = snapshot.get("resource")
    resource_attributes = (
        resource.get("attributes", {}) if isinstance(resource, dict) else {}
    )
    return {
        "signal": "span",
        "name": safe_name,
        "attributes": safe_attributes,
        "events": [],
        "links": [],
        "resource": {
            "attributes": {
                "service.name": "GhostErase",
                "telemetry.sdk.name": "neatlogs",
            }
            if isinstance(resource_attributes, dict)
            else {}
        },
        "status": {
            "code": (
                snapshot.get("status", {}).get("code", "UNSET")
                if isinstance(snapshot.get("status"), dict)
                and snapshot.get("status", {}).get("code") in {"UNSET", "OK", "ERROR"}
                else "UNSET"
            ),
            "description": None,
        },
    }


class OperationSpan:
    def __init__(self, span: Any | None, started: float) -> None:
        self._span = span
        self._started = started
        self._finished = False

    def set_result(self, status: str, **attributes: Any) -> None:
        self._finished = True
        if self._span is None:
            return
        safe_status = status if status in {"pass", "fail", "blocked", "not_verified"} else "fail"
        safe_attributes = {
            "result_status": safe_status,
            "latency_ms": max(0, int((time.monotonic() - self._started) * 1000)),
        }
        for key, value in attributes.items():
            safe_value = _safe_value(key, value)
            if safe_value is not None:
                safe_attributes[key] = safe_value
        try:
            for key, value in safe_attributes.items():
                self._span.set_attribute(key, value)
        except Exception:
            return

    def finish(self) -> None:
        if not self._finished:
            self.set_result("pass")


@contextmanager
def telemetry_span(
    name: str,
    kind: str,
    **attributes: Any,
) -> Iterator[OperationSpan]:
    global _INITIALIZATION_ERROR
    started = time.monotonic()
    safe_attributes = {
        key: value
        for key, value in (
            (key, _safe_value(key, value)) for key, value in attributes.items()
        )
        if value is not None
    }
    if not _initialization_status():
        yield OperationSpan(None, started)
        return

    try:
        import neatlogs
        trace_context = neatlogs.trace(
            name if name in _SAFE_SPAN_NAMES else "ghosterase",
            kind=kind,
            capture_stdout=False,
            **safe_attributes,
        )
        span = trace_context.__enter__()
        _INITIALIZATION_ERROR = None
    except Exception as error:
        _INITIALIZATION_ERROR = type(error).__name__
        yield OperationSpan(None, started)
        return

    operation = OperationSpan(span, started)
    try:
        yield operation
    except BaseException as error:
        operation.set_result("fail", error_category=type(error).__name__)
        try:
            trace_context.__exit__(*sys.exc_info())
        except Exception:
            pass
        raise
    else:
        operation.finish()
        try:
            trace_context.__exit__(None, None, None)
        except Exception:
            pass


def neatlogs_status() -> dict[str, Any]:
    if not settings.neatlogs_api_key:
        return {
            "status": "blocked",
            "verified": False,
            "detail": "NEATLOGS_API_KEY is not configured; authenticated trace delivery and readback were not run.",
        }
    if _INITIALIZATION_ERROR:
        return {
            "status": "blocked",
            "verified": False,
            "detail": f"Neatlogs SDK initialization failed ({_INITIALIZATION_ERROR}).",
        }
    return {
        "status": "unverified",
        "verified": False,
        "detail": "SDK spans are enabled, but authenticated delivery and project trace readback have not been verified.",
    }


def shutdown_telemetry() -> None:
    if not _INITIALIZED:
        return
    try:
        import neatlogs

        neatlogs.shutdown()
    except Exception:
        return
