from __future__ import annotations

from ghosterase.service import GhostEraseService
from ghosterase.vectorstore import VectorStore, VectorStoreUnavailableError


def test_legacy_rag_operations_are_blocked_without_real_providers(tmp_path) -> None:
    service = GhostEraseService(db_path=str(tmp_path / "ghosterase.sqlite3"), chroma_path=str(tmp_path / "chroma"))

    ingested = service.ingest_document("Prototype content.", filename="document.txt")
    search = service.search("Prototype")
    assert ingested["status"] == "BLOCKED"
    assert search["status"] == "BLOCKED"


def test_vector_store_never_falls_back_to_synthetic_vectors(tmp_path) -> None:
    vector_store = VectorStore(tmp_path / "chroma")

    try:
        vector_store.add_document("document-id", "Prototype content.", "unit-test")
    except VectorStoreUnavailableError as error:
        assert "ChromaDB" in str(error)
    else:
        raise AssertionError("Vector operation unexpectedly succeeded without ChromaDB and real embeddings.")


def test_delete_and_verify_do_not_claim_success_without_rag(tmp_path) -> None:
    service = GhostEraseService(db_path=str(tmp_path / "ghosterase.sqlite3"), chroma_path=str(tmp_path / "chroma"))
    service.storage.add_document("document-id", "document.txt", "unit-test", "Prototype content.")

    deleted = service.delete_document("document-id")
    verification = service.verify_document("document-id")

    assert deleted["status"] == "BLOCKED"
    assert verification["summary"]["status"] == "BLOCKED"
    assert service.storage.get_document("document-id")["status"] == "stored"
