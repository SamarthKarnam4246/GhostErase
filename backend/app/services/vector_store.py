from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

import chromadb
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

from backend.app.core.config import settings
from backend.app.integrations.telemetry import telemetry_span


class ChromaVectorStore:
    def __init__(self, path: str | Path | None = None, collection_name: str | None = None) -> None:
        self.path = Path(path) if path is not None else settings.chroma_dir
        self.path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(self.path))
        self.collection = self.client.get_or_create_collection(
            name=collection_name or settings.chroma_collection,
            metadata={"hnsw:space": "cosine"},
            embedding_function=DefaultEmbeddingFunction(),
        )

    @staticmethod
    def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 120) -> list[str]:
        if chunk_size <= overlap or not text.strip():
            raise ValueError("Text must be non-empty and chunk size must exceed overlap.")
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            if end < len(text):
                boundary = text.rfind(" ", start + chunk_size // 2, end)
                if boundary > start:
                    end = boundary
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end == len(text):
                break
            start = max(start + 1, end - overlap)
        return chunks

    @staticmethod
    def _chunk_id(document_id: str, index: int, text: str) -> str:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return str(uuid.uuid5(uuid.UUID(document_id), f"{index}:{digest}"))

    def add_document(self, document_id: str, text: str, source: str, filename: str) -> list[str]:
        chunks = self.chunk_text(text)
        ids = [self._chunk_id(document_id, index, chunk) for index, chunk in enumerate(chunks)]
        with telemetry_span(
            "rag.embedding",
            kind="EMBEDDING",
            document_id=document_id,
            operation_type="document_index",
            chunk_count=len(chunks),
            model_identifier="all-MiniLM-L6-v2",
        ) as span:
            self.collection.add(
                ids=ids,
                documents=chunks,
                metadatas=[
                    {
                        "document_id": document_id,
                        "chunk_index": index,
                        "source": source,
                        "filename": filename,
                    }
                    for index in range(len(chunks))
                ],
            )
            span.set_result("pass", chunk_count=len(chunks))
        return ids

    def document_chunks(self, document_id: str) -> dict[str, list[Any]]:
        with telemetry_span(
            "deletion.residual_check",
            kind="RETRIEVER",
            document_id=document_id,
            operation_type="chromadb_residual_check",
        ) as span:
            result = self.collection.get(
                where={"document_id": document_id},
                include=["metadatas"],
            )
            span.set_result(
                "pass" if not result.get("ids") else "fail",
                result_count=len(result.get("ids", [])),
            )
            return result

    def delete_chunks(self, document_id: str, chunk_ids: list[str]) -> dict[str, Any]:
        if not chunk_ids:
            raise ValueError("No known chunk IDs were recorded for this document.")
        with telemetry_span(
            "deletion.vector_removal",
            kind="TOOL",
            document_id=document_id,
            operation_type="chromadb_delete",
            chunk_count=len(chunk_ids),
        ) as span:
            selected = self.collection.get(
                ids=chunk_ids,
                where={"document_id": document_id},
                include=["metadatas"],
            )
            selected_ids = selected.get("ids", [])
            if set(selected_ids) != set(chunk_ids):
                span.set_result("fail", error_category="ChunkSelectionMismatch")
                raise RuntimeError("The selected chunk IDs do not exactly match the target document.")
            self.collection.delete(ids=selected_ids, where={"document_id": document_id})
            span.set_result("pass", chunk_count=len(selected_ids))
            return {"requested_chunk_ids": selected_ids, "api_response_received": True}

    def query(self, query_text: str, limit: int = 5) -> list[dict[str, Any]]:
        bounded_limit = min(max(1, limit), 20)
        with telemetry_span(
            "rag.vector_query",
            kind="RETRIEVER",
            operation_type="vector_retrieval",
            top_k=bounded_limit,
            model_identifier="all-MiniLM-L6-v2",
        ) as span:
            result = self.collection.query(
                query_texts=[query_text],
                n_results=bounded_limit,
                include=["documents", "metadatas", "distances"],
            )
            ids = (result.get("ids") or [[]])[0]
            documents = (result.get("documents") or [[]])[0]
            metadatas = (result.get("metadatas") or [[]])[0]
            distances = (result.get("distances") or [[]])[0]
            hits = [
                {
                    "chunk_id": chunk_id,
                    "document_id": metadata["document_id"],
                    "chunk_index": metadata["chunk_index"],
                    "content": document,
                    "distance": distance,
                }
                for chunk_id, document, metadata, distance in zip(ids, documents, metadatas, distances)
                if metadata and metadata.get("document_id") and document is not None
            ]
            span.set_result("pass", result_count=len(hits))
            return hits
