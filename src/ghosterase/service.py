from __future__ import annotations

from typing import Any

from .model import HostedModelClient
from .neatlogs import send_trace
from .storage import SQLiteStore
from .vectorstore import VectorStore


RAG_BLOCKED_REASON = (
    "RAG operations are blocked until persistent ChromaDB and a real embedding provider are available."
)


class GhostEraseService:
    def __init__(self, db_path: str | None = None, chroma_path: str | None = None) -> None:
        self.storage = SQLiteStore(db_path)
        self.vector_store = VectorStore(chroma_path)
        self.model_client = HostedModelClient()

    def ingest_document(self, text: str, source: str = "upload", filename: str | None = None) -> dict[str, Any]:
        return {"status": "BLOCKED", "reason": RAG_BLOCKED_REASON}

    def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        return {"status": "BLOCKED", "reason": RAG_BLOCKED_REASON, "results": []}

    def delete_document(self, document_id: str) -> dict[str, Any]:
        if self.storage.get_document(document_id) is None:
            return {"document_id": document_id, "status": "NOT_VERIFIED", "reason": "Document not found."}
        return {"document_id": document_id, "status": "BLOCKED", "reason": RAG_BLOCKED_REASON}

    def verify_document(self, document_id: str) -> dict[str, Any]:
        document = self.storage.get_document(document_id)
        if document is None:
            return {
                "document_id": document_id,
                "summary": {"status": "NOT_VERIFIED", "reason": "Document record was not found."},
                "checks": [],
            }

        return {
            "document_id": document_id,
            "summary": {"status": "BLOCKED", "reason": RAG_BLOCKED_REASON},
            "checks": [
                {"name": "persistent_vector_index", "status": "BLOCKED"},
                {"name": "embedding_provider", "status": "BLOCKED"},
            ],
        }

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "hosted_model_verified": self.model_client.connected,
            "neatlogs": send_trace("healthcheck", {"service": "ghosterase"}),
        }
