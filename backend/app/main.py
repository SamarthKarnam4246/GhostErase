from __future__ import annotations

import ipaddress
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

from backend.app.core.config import settings
from backend.app.integrations.cfo_ai import CfoAiIntegration, CfoIntegrationError
from backend.app.integrations.telemetry import (
    initialize_telemetry,
    neatlogs_status,
    request_workflow_name,
    shutdown_telemetry,
    telemetry_span,
)
from backend.app.services.document_service import DocumentService


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_telemetry()
    try:
        yield
    finally:
        await cfo_ai.shutdown()
        shutdown_telemetry()


app = FastAPI(title="GhostErase Backend", lifespan=lifespan)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"],
)
service = DocumentService()
cfo_ai = CfoAiIntegration()


@app.middleware("http")
async def trace_application_workflows(request, call_next):
    workflow_name = request_workflow_name(request.method, request.url.path)
    if workflow_name is None:
        return await call_next(request)

    with telemetry_span(
        workflow_name,
        kind="WORKFLOW",
        **{"neatlogs.workflow.name": workflow_name},
    ) as span:
        try:
            response = await call_next(request)
        except Exception as error:
            span.set_result("fail", error_category=type(error).__name__)
            raise
        if response.status_code < 400:
            result_status = "pass"
        elif response.status_code in {401, 403, 409}:
            result_status = "blocked"
        else:
            result_status = "fail"
        span.set_result(
            result_status,
            error_category="HTTPError" if response.status_code >= 400 else None,
        )
        return response


@app.middleware("http")
async def local_only_security_headers(request, call_next):
    client = request.client
    try:
        if client is None or not ipaddress.ip_address(client.host).is_loopback:
            return JSONResponse(status_code=403, content={"detail": "GhostErase accepts loopback clients only."})
    except ValueError:
        return JSONResponse(status_code=403, content={"detail": "GhostErase accepts loopback clients only."})

    origin = request.headers.get("origin")
    if origin:
        parsed_origin = urlsplit(origin)
        try:
            allowed_origin = (
                parsed_origin.scheme in {"http", "https"}
                and parsed_origin.hostname is not None
                and (
                    parsed_origin.hostname.lower() == "localhost"
                    or ipaddress.ip_address(parsed_origin.hostname).is_loopback
                )
            )
        except ValueError:
            allowed_origin = parsed_origin.hostname is not None and parsed_origin.hostname.lower() == "localhost"
        if not allowed_origin:
            return JSONResponse(status_code=403, content={"detail": "Cross-origin requests are not accepted."})

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


class SearchPayload(BaseModel):
    query: str
    limit: int = Field(default=5, ge=1, le=20)


class CfoWorkspacePayload(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=256)


class CfoReadPayload(BaseModel):
    tool_name: str
    arguments: dict = Field(default_factory=dict)


@app.get("/api/v1/health")
def health() -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "vector_store": "ChromaDB PersistentClient",
        "embedding_provider": "ChromaDB DefaultEmbeddingFunction (all-MiniLM-L6-v2 ONNX)",
        "deletion_authorization_configured": bool(settings.delete_auth_token),
        "guided_demo_enabled": settings.demo_mode,
        "neatlogs": neatlogs_status(),
    }


@app.get("/health")
def legacy_health() -> dict:
    return health()


@app.get("/api/v1/integrations/neatlogs")
def neatlogs_integration_status() -> dict:
    return neatlogs_status()


@app.get("/api/v1/dashboard")
async def dashboard() -> dict:
    cfo_status = await cfo_ai.status()
    neatlogs = neatlogs_status()
    neatlogs_state = neatlogs["status"]
    if not settings.neatlogs_api_key:
        neatlogs_state = "not_configured"
    elif neatlogs_state == "unverified":
        neatlogs_state = "unverified"
    else:
        neatlogs_state = "blocked"
    cfo_state = cfo_status["state"]
    financial_result = cfo_status.get("financial_result")
    if financial_result is not None:
        financial_impact = financial_result
    elif cfo_state != "verified":
        financial_impact = {
            "status": "unavailable",
            "detail": "CFO.ai is not connected and verified; no financial data is available.",
            "result": None,
        }
    else:
        financial_impact = {
            "status": "insufficient_financial_data",
            "detail": "The connected workspace has not provided relevant financial-impact data.",
            "result": None,
        }
    return {
        **service.dashboard_data(),
        "integrations": {
            "neatlogs_runtime": {
                "state": neatlogs_state,
                "verified": neatlogs["verified"],
                "detail": neatlogs["detail"],
            },
            "neatlogs_developer_mcp": {
                "state": "not_configured",
                "verified": False,
                "detail": "No Neatlogs developer MCP connection is configured in GhostErase.",
            },
            "entire_copilot_cli": {
                "state": "not_configured",
                "verified": False,
                "detail": "Entire/Copilot CLI capture is a development-provenance workflow, not a GhostErase runtime integration; no capture is verified.",
            },
            "cfo_ai_runtime": {
                "state": cfo_state,
                "verified": cfo_status["verified"],
                "detail": cfo_status["detail"],
            },
        },
        "financial_impact": financial_impact,
    }


@app.get("/api/v1/integrations/cfo")
async def cfo_status() -> dict:
    return await cfo_ai.status()


def _cfo_error_leaves(error: BaseException):
    if isinstance(error, BaseExceptionGroup):
        for sub_error in error.exceptions:
            yield from _cfo_error_leaves(sub_error)
    else:
        yield error


def _cfo_integration_leaf(error: BaseException) -> CfoIntegrationError | None:
    """Find our sanitized error inside async-session exception groups.

    Errors raised while a live MCP client session is open can arrive wrapped
    (anyio task machinery), bypassing `except CfoIntegrationError`. Unwrap
    single or nested groups; anything else re-raises untouched.
    """
    for leaf in _cfo_error_leaves(error):
        if isinstance(leaf, CfoIntegrationError):
            return leaf
    return None


@app.post("/api/v1/integrations/cfo/connect")
async def cfo_connect() -> dict:
    try:
        return await cfo_ai.start_connect()
    except CfoIntegrationError as error:
        status_code = 409 if error.category == "AuthorizationAlreadyPending" else 503
        raise HTTPException(
            status_code=status_code,
            detail=error.public_detail,
        ) from error


@app.post("/api/v1/integrations/cfo/workspace")
async def cfo_select_workspace(payload: CfoWorkspacePayload) -> dict:
    try:
        return await cfo_ai.select_workspace(payload.workspace_id)
    except CfoIntegrationError as error:
        status_code = 400 if error.category in {"UnknownWorkspace", "WorkspaceSelectionUnavailable"} else 503
        raise HTTPException(status_code=status_code, detail=error.public_detail) from error
    except BaseExceptionGroup as error:
        leaf = _cfo_integration_leaf(error)
        if leaf is None:
            raise
        status_code = 400 if leaf.category in {"UnknownWorkspace", "WorkspaceSelectionUnavailable"} else 503
        raise HTTPException(status_code=status_code, detail=leaf.public_detail) from error


@app.post("/api/v1/integrations/cfo/read")
async def cfo_read(payload: CfoReadPayload) -> dict:
    try:
        return await cfo_ai.read_financial_data(payload.tool_name, payload.arguments)
    except CfoIntegrationError as error:
        status_code = 403 if error.category == "ReadToolNotAllowed" else 409
        raise HTTPException(status_code=status_code, detail=error.public_detail) from error
    except BaseExceptionGroup as error:
        leaf = _cfo_integration_leaf(error)
        if leaf is None:
            raise
        status_code = 403 if leaf.category == "ReadToolNotAllowed" else 409
        raise HTTPException(status_code=status_code, detail=leaf.public_detail) from error


@app.delete("/api/v1/integrations/cfo")
async def cfo_disconnect() -> dict:
    try:
        return await cfo_ai.disconnect()
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="CFO.ai credentials could not be safely removed from the OS credential vault.",
        ) from error


@app.post("/api/v1/documents/upload")
async def upload_document(file: UploadFile = File(...)) -> dict:
    filename = file.filename or ""
    content_type = (file.content_type or "").lower()
    suffix = filename.replace("\\", "/").rsplit(".", 1)[-1].lower() if "." in filename else ""
    accepted_types = {"txt": {"text/plain"}, "md": {"text/plain", "text/markdown", "text/x-markdown"}}
    if suffix not in accepted_types or content_type not in accepted_types[suffix]:
        raise HTTPException(status_code=415, detail="Upload must be a .txt or .md file with a supported text content type.")
    payload = await file.read(settings.max_upload_bytes + 1)
    if len(payload) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Upload exceeds the 5 MiB limit.")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=400, detail="Upload must contain valid UTF-8 text.") from error
    if not text.strip():
        raise HTTPException(status_code=400, detail="Document content cannot be empty.")
    try:
        return service.create_document(text, "upload", filename)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=503, detail="Document indexing failed; no successful upload was recorded.") from error


@app.post("/api/v1/documents/{document_id}/delete")
def delete_document(document_id: str, x_delete_token: str | None = Header(default=None)) -> dict:
    result = service.delete_document(document_id, x_delete_token)
    if result["status"] == "blocked" and "operation_id" not in result:
        raise HTTPException(status_code=403, detail=result["reason"])
    return result


@app.post("/api/v1/documents/{document_id}/verify")
def verify_document(document_id: str) -> dict:
    return service.verify_document(document_id)


@app.post("/api/v1/demo/runs")
def start_guided_demo(x_delete_token: str | None = Header(default=None)) -> dict:
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Guided demo mode is disabled.")
    try:
        return service.start_guided_demo(x_delete_token)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="The synthetic guided demo could not complete its retrieval and residual-detection setup.",
        ) from error


@app.post("/api/v1/demo/runs/{operation_id}/remediate")
def remediate_guided_demo(
    operation_id: str,
    x_delete_token: str | None = Header(default=None),
) -> dict:
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Guided demo mode is disabled.")
    try:
        return service.remediate_guided_demo(operation_id, x_delete_token)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="The authorized synthetic-demo remediation could not be completed.",
        ) from error


@app.get("/api/v1/documents")
def list_documents() -> list[dict]:
    return service.list_documents()


@app.get("/api/v1/documents/{document_id}/lineage")
def document_lineage(document_id: str) -> dict:
    detail = service.document_detail(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return detail


@app.get("/api/v1/deletion-operations")
def list_deletion_operations() -> list[dict]:
    return service.dashboard_data()["recent_operations"]


@app.post("/api/v1/search")
def search_documents(payload: SearchPayload) -> dict:
    if not payload.query.strip():
        raise HTTPException(status_code=400, detail="Search query cannot be empty.")
    try:
        return {"query": payload.query, "results": service.search(payload.query, payload.limit)}
    except Exception as error:
        raise HTTPException(status_code=503, detail="ChromaDB retrieval failed.") from error


@app.get("/api/v1/deletion-operations/{operation_id}")
def deletion_report(operation_id: str) -> dict:
    report = service.deletion_report(operation_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Deletion operation not found.")
    return report


@app.post("/api/v1/deletion-operations/{operation_id}/explain")
def explain_deletion_report(operation_id: str) -> dict:
    """Advisory AI explanation. Never changes the verified report status."""
    explanation = service.explain_operation(operation_id)
    if explanation is None:
        raise HTTPException(status_code=404, detail="Deletion operation not found.")
    return explanation
