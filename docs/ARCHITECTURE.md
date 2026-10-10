# Architecture, data flow, and storage

GhostErase is a **local-first MVP**, not a production deployment. The frontend is a React/TypeScript SPA served by Vite during development. It calls a FastAPI backend through the Vite `/api` proxy. The backend must bind to loopback (`127.0.0.1`); it rejects non-loopback clients and untrusted origins.

## Components

| Component | Responsibility | Implementation |
|---|---|---|
| React dashboard | Upload/search documents, show persistent status and deletion reports, expose integration state, display returned CFO.ai data only when authorized | `frontend/src/App.tsx`, `frontend/src/api/client.ts`, `frontend/src/types.ts` |
| FastAPI API | Validate HTTP input, enforce local-only ingress, route operations to services | `backend/app/main.py` |
| Document service | Safe source file creation, metadata persistence, known-ID retrieval filtering, deterministic deletion orchestration, report assembly, dashboard aggregates | `backend/app/services/document_service.py` |
| SQL database | Document metadata and body while active; deletion operations and individual check evidence | `backend/app/db/session.py`, `backend/app/models/` |
| ChromaDB | Persistent vectors, text chunks and metadata; uses Chroma's default `all-MiniLM-L6-v2` ONNX embedding function | `backend/app/services/vector_store.py` |
| Neatlogs SDK | Manual metadata-only backend traces through an allowlisted export boundary | `backend/app/integrations/telemetry.py`, `backend/app/integrations/neatlogs.py` |
| CFO.ai MCP client | Optional runtime OAuth, secure local credential storage, live tool discovery, read-only allowlist | `backend/app/integrations/cfo_ai.py` |

Entire/Copilot CLI is a development-provenance tool, not an application runtime service. Its integration is not configured or verified here.

## Ingestion and retrieval data flow

1. `POST /api/v1/documents/upload` accepts a bounded `.txt` or `.md` upload with supported text content type and valid UTF-8. It returns sanitized errors, not file contents.
2. `DocumentService` makes a UUID document directory, normalizes the display filename, writes the source file using exclusive creation and restrictive POSIX modes where available, then chunks deterministically.
3. `ChromaVectorStore` uses the actual local Chroma ONNX embedding function and stores chunk IDs plus document ID, chunk index, source, and display filename through Chroma's public API.
4. SQLAlchemy records the ID, safe display name, source, active document body, SHA-256 content hash, chunk IDs, source path, status, and creation time.
5. `/api/v1/search` queries Chroma and returns only `(document_id, chunk_id)` pairs that exist in SQL metadata and are not deleted.

This local ONNX path was exercised by this worktree's Playwright test. It is not the separately requested approved hosted embedding provider. The approved route was reported as HTTP 403 in a separate audit; dimensions remain unverified. See `ACCEPTANCE.md`.

## Deletion data flow

1. The caller supplies the configured `DELETE_AUTH_TOKEN` in `X-Delete-Token`; absent/incorrect authorization is rejected before an operation is created.
2. The service creates a persisted operation with the target document ID and its recorded chunk IDs. It does not copy the document body into the operation.
3. Each source, vector-delete, Chroma residual, application retrieval, and SQL metadata-clear check is executed and stored independently with timestamp, method, result, identifiers, and sanitized error when applicable.
4. Chroma operations select both known chunk IDs and the target document ID using supported APIs. Source deletion requires the expected document-scoped path and rejects symlinks/path mismatches.
5. SQL body clearing and final `pass` require all preceding required checks to pass. Failures preserve evidence/content needed for recovery and cannot become a green result.
6. The report is assembled from the persisted operation/check records. It establishes configured API-level accessibility only—not SSD, backups, journal, or universal physical erasure.

## CFO.ai boundary

The backend uses the official MCP SDK OAuth provider, authorization code + S256 PKCE, the SDK's state/issuer checks, Streamable HTTP, and a loopback callback. Registration and tokens are stored in the OS credential vault, never returned to the frontend. Only discovered, schema-validated read tools (`list_orgs`, `inspect_pages`, `inspect_table_blocks`) are permitted. Financial responses remain in the process for the panel and are not put in the document index. No live authorization or model read has yet been verified.

## SQL schema

The ORM definitions are in `backend/app/models/document.py` and `backend/app/models/deletion.py`.

### `documents`

| Column | Meaning |
|---|---|
| `id` | UUID document primary key |
| `filename` | Safe display filename |
| `source` | Ingestion source label |
| `content` | Active source text, cleared only after all required deletion checks pass |
| `content_hash` | SHA-256 of source text |
| `chunk_ids` | JSON-encoded list of Chroma chunk IDs |
| `source_file_path` | Application-managed source file path |
| `status` | Index/deletion workflow status |
| `created_at` | UTC ISO-8601 ingestion timestamp; nullable for legacy rows |
| `deleted_at` | Deletion timestamp field |

### `deletion_operations`

`id` primary key, `document_id` foreign key, JSON-encoded `chunk_ids`, `status`, `created_at`, and nullable `completed_at`.

### `deletion_checks`

`id` primary key, operation/document foreign keys, JSON-encoded `chunk_ids`, `layer`, `method`, `status`, JSON-encoded `actual_result`, nullable sanitized `error_detail`, and `checked_at`.

## Database initialization and migration status

Importing `backend.app.db.session` calls SQLAlchemy `Base.metadata.create_all()` and applies additive startup checks for the legacy `documents.content_hash`, `chunk_ids`, `source_file_path`, and `created_at` columns. These are **not versioned Alembic migrations**; no Alembic migration scripts/revision history are provided even though Alembic is declared in project metadata. Back up the local DB before upgrades and do not treat this as a production migration strategy.

Default local paths are `backend/data/ghosterase.sqlite3`, `backend/data/uploads`, and `backend/data/chroma`. E2E tests override all three to unique temporary directories and remove those paths after each run.
