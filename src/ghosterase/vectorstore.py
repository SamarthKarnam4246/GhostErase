from __future__ import annotations

from pathlib import Path


class VectorStoreUnavailableError(RuntimeError):
    pass


class VectorStore:
    def __init__(self, chroma_path: str | Path | None = None) -> None:
        self.chroma_path = Path(chroma_path) if chroma_path is not None else Path("data/chroma")

    @staticmethod
    def _unavailable() -> None:
        raise VectorStoreUnavailableError(
            "Vector operations are disabled: persistent ChromaDB and a real embedding provider are not configured."
        )

    def add_document(self, document_id: str, text: str, source: str) -> None:
        self._unavailable()

    def search(self, query: str, limit: int = 5) -> list[dict]:
        self._unavailable()

    def delete_document(self, document_id: str) -> None:
        self._unavailable()

    def remaining(self, document_id: str) -> list[dict]:
        self._unavailable()
