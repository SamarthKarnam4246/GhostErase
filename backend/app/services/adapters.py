from __future__ import annotations

from typing import Any, Protocol

from backend.app.services.cache_store import AppCache


class DeletionAdapter(Protocol):
    """Interface for storage-specific deletion connectors.

    Future connectors (conversation memory, exports, …) implement this
    interface and register below. Only adapters with real implementations
    are registered -- anything else stays unsupported/unverified.
    """

    layer: str

    def remove(self, document_id: str) -> dict[str, Any]:
        """Delete/invalidate the document's artifacts. Returns evidence."""
        ...

    def residuals(self, document_id: str) -> dict[str, Any]:
        """Return evidence of artifacts still present for the document."""
        ...


class CacheDeletionAdapter:
    """Real adapter for the application-owned demo cache."""

    layer = "app_cache"

    def __init__(self, cache: AppCache) -> None:
        self.cache = cache

    def remove(self, document_id: str) -> dict[str, Any]:
        return self.cache.invalidate_document(document_id)

    def residuals(self, document_id: str) -> dict[str, Any]:
        return self.cache.residuals(document_id)


def registered_adapters(cache: AppCache) -> dict[str, DeletionAdapter]:
    """Adapters with real implementations. Nothing else is registered."""
    adapter: DeletionAdapter = CacheDeletionAdapter(cache)
    return {adapter.layer: adapter}
