from __future__ import annotations

import uuid

import pytest

from backend.app.services.document_service import DocumentService
from backend.app.services.vector_store import ChromaVectorStore

pytestmark = pytest.mark.unit


def test_chunking_is_deterministic_and_respects_overlap() -> None:
    text = " ".join(f"token-{index}" for index in range(240))
    first = ChromaVectorStore.chunk_text(text, chunk_size=180, overlap=24)
    second = ChromaVectorStore.chunk_text(text, chunk_size=180, overlap=24)
    assert first == second
    assert len(first) > 1
    assert all(chunk.strip() for chunk in first)
    assert any(set(first[index].split()) & set(first[index + 1].split()) for index in range(len(first) - 1))
    with pytest.raises(ValueError):
        ChromaVectorStore.chunk_text(text, chunk_size=24, overlap=24)


def test_chunk_ids_are_deterministic_and_document_scoped() -> None:
    first_document = str(uuid.uuid4())
    other_document = str(uuid.uuid4())
    first = ChromaVectorStore._chunk_id(first_document, 0, "synthetic chunk")
    assert ChromaVectorStore._chunk_id(first_document, 0, "synthetic chunk") == first
    assert ChromaVectorStore._chunk_id(first_document, 1, "synthetic chunk") != first
    assert ChromaVectorStore._chunk_id(other_document, 0, "synthetic chunk") != first


def test_display_filenames_cannot_escape_document_storage() -> None:
    assert DocumentService._safe_filename("../../private.txt") == "private.txt"
    assert DocumentService._safe_filename("C:\\private\\notes.MD") == "notes.md"
    with pytest.raises(ValueError):
        DocumentService._safe_filename("../../private.pdf")


def test_application_retrieval_filters_unknown_document_and_chunk_ids() -> None:
    class MockVectorStore:
        def query(self, _query: str, _limit: int) -> list[dict]:
            return [
                {
                    "chunk_id": "known-chunk",
                    "document_id": known_id,
                    "chunk_index": 0,
                    "content": "known indexed content",
                    "distance": 0.1,
                },
                {
                    "chunk_id": "invented-chunk",
                    "document_id": known_id,
                    "chunk_index": 1,
                    "content": "unrecognized chunk",
                    "distance": 0.2,
                },
                {
                    "chunk_id": "foreign-chunk",
                    "document_id": str(uuid.uuid4()),
                    "chunk_index": 0,
                    "content": "foreign document",
                    "distance": 0.3,
                },
            ]

    known_id = str(uuid.uuid4())
    service = DocumentService.__new__(DocumentService)
    service.vector_store = MockVectorStore()
    from types import SimpleNamespace

    # Empty cache: exercise the live-query filtering path below.
    service.cache = SimpleNamespace(get=lambda _query: [], put=lambda _query, _hits: [])

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def query(self, _model):
            return self

        def filter(self, _condition):
            return self

        def all(self):
            from types import SimpleNamespace

            import json

            return [SimpleNamespace(id=known_id, chunk_ids=json.dumps(["known-chunk"]), status="indexed")]

    service.session_factory = FakeSession
    hits = service._search("query", 5)
    assert [hit["chunk_id"] for hit in hits] == ["known-chunk"]


def test_overall_gate_requires_all_checks_and_never_passes_on_unrun() -> None:
    derive = DocumentService._derive_overall
    assert derive({"pass"}, 1, 1) == "pass"
    assert derive({"pass"}, 6, 6) == "pass"
    assert derive({"pass", "blocked"}, 6, 6) == "blocked"
    assert derive({"pass", "fail"}, 6, 6) == "fail"
    assert derive({"pass"}, 6, 5) == "blocked"
    assert derive(set(), 6, 0) == "blocked"
    assert derive({"not_verified"}, 1, 1) == "blocked"


def test_safe_error_hides_content_and_credentials() -> None:
    marker = f"secret-{uuid.uuid4().hex}"
    try:
        raise RuntimeError(f"failure with {marker} and token hunter2")
    except RuntimeError as error:
        sanitized = DocumentService._safe_error(error)
    assert marker not in sanitized
    assert "hunter2" not in sanitized
    assert "RuntimeError" in sanitized


def test_retrieval_probe_windows_cover_head_middle_and_tail() -> None:
    long_text = " ".join(f"token-{index}" for index in range(500))
    assert len(long_text) > 2500
    windows = DocumentService._retrieval_probe_windows(long_text)
    assert len(windows) == 3
    assert windows[0] == long_text[:1000]
    assert windows[2] == long_text[-1000:]
    assert DocumentService._retrieval_probe_windows("short") == ["short"]
    assert len(DocumentService._retrieval_probe_windows("   ")) == 1
