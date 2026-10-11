# Architecture, data flow, and storage

GhostErase is a **local-first MVP**, not a production deployment. The frontend is a React/TypeScript SPA served by Vite during development. It calls a FastAPI backend through the Vite `/api` proxy. The backend must bind to loopback (`127.0.0.1`); it rejects non-loopback clients and untrusted origins.

## Product position and boundaries

GhostErase is an AI/data privacy lifecycle assurance platform whose initial wedge is evidence-backed verification for RAG and AI pipelines. The MVP tracks known source-to-derived-artifact lineage and executes deletion only against real implemented local stores. Later privacy actions and connectors require their own policies, authorization, capabilities, verification criteria, and tests. Unknown systems remain coverage gaps; reports are bounded to a versioned declared scope and the systems actually inspected. There is no universal discovery, erasure, or model-unlearning guarantee.

Two distinct bounded agent roles (privacy-action planning and residual investigation) are a target, not the current single advisory explainer. Agents may propose or investigate; deterministic services alone authorize, execute, verify, and set status. CFO.ai is sponsor/business-planning tooling, not a normal customer runtime feature; its current panel is legacy scope for removal in the privacy-operations workspace phase.

## Components

| Component | Responsibility | Implementation |
|---|---|---|
| React dashboard | Upload/search documents, show persistent status and deletion reports, expose integration state, display returned CFO.ai data only when authorized | `frontend/src/App.tsx`, `frontend/src/api/client.ts`, `frontend/src/types.ts` |
| FastAPI API | Validate HTTP input, enforce local-only ingress, route operations to services | `backend/app/main.py` |
| Document service | Safe source file creation, metadata persistence, known-ID retrieval filtering, deterministic deletion orchestration, report assembly, dashboard aggregates | `backend/app/services/document_service.py` |
| SQL database | Document metadata and body while active; deletion operations and individual check evidence | `backend/app/db/session.py`, `backend/app/models/` |
| Lineage registry | Current-state artifact pointers (source/chunk/cache/sql) with stable IDs and parent links; evidence stays in append-only checks | `backend/app/models/lineage.py` (`derived_artifacts`) |
| Application cache | Persistent per-query retrieval cache storing query hashes + result IDs only, for the stale-cache demo | `backend/app/services/cache_store.py` (`cache_entries`, `AppCache`) |
| Deletion adapters | Storage-connector interface; only real implementations registered (`app_cache`) | `backend/app/services/adapters.py` |
| ChromaDB | Persistent vectors, text chunks and metadata; uses Chroma's default `all-MiniLM-L6-v2` ONNX embedding function | `backend/app/services/vector_store.py` |
| Neatlogs SDK | Manual metadata-only backend traces through an allowlisted export boundary | `backend/app/integrations/telemetry.py`, `backend/app/integrations/neatlogs.py` |
| CFO.ai MCP client | Existing experimental sponsor-tooling panel; OAuth, secure local credential storage, live tool discovery, read-only allowlist. Not part of the target customer runtime. | `backend/app/integrations/cfo_ai.py` |

Entire/Copilot CLI is a development-provenance tool, not an application runtime service. Its integration is not configured or verified here.

## Ingestion and retrieval data flow

1. `POST /api/v1/documents/upload` accepts a bounded `.txt` or `.md` upload with supported text content type and valid UTF-8. It returns sanitized errors, not file contents.
2. `DocumentService` makes a UUID document directory, normalizes the display filename, writes the source file using exclusive creation and restrictive POSIX modes where available, then chunks deterministically.
3. `ChromaVectorStore` uses the actual local Chroma ONNX embedding function and stores chunk IDs plus document ID, chunk index, source, and display filename through Chroma's public API.
4. SQLAlchemy records the ID, safe display name, source, active document body, SHA-256 content hash, chunk IDs, source path, status, and creation time, plus lineage rows for the source file, each chunk (exact vector ID, `chromadb:<collection>` backend), and the SQL content row.
5. `/api/v1/search` first consults the application cache by query hash and resolves stored IDs against the real collection; on a miss it queries Chroma and stores per-document cache rows (query hash + chunk IDs + distances, never raw text). Results are filtered to `(document_id, chunk_id)` pairs that exist in SQL metadata and are not deleted.

This local ONNX path was exercised by this worktree's Playwright test. It is not the separately requested approved hosted embedding provider. The approved route was reported as HTTP 403 in a separate audit; dimensions remain unverified. See `ACCEPTANCE.md`.

## Deletion data flow

1. The caller supplies the configured `DELETE_AUTH_TOKEN` in `X-Delete-Token`; absent/incorrect authorization is rejected before an operation is created.
2. The service creates a persisted operation with the target document ID and its recorded chunk IDs. It does not copy the document body into the operation. Documents created before the registry gain lineage rows here via idempotent backfill (stable artifact IDs, missing rows only).
3. Each source, vector-delete, Chroma residual, application-cache invalidate/residual, application retrieval, and SQL metadata-clear check is executed and stored independently with timestamp, method, result, identifiers, and sanitized error when applicable. `pass` requires all six.
4. Chroma operations select both known chunk IDs and the target document ID using supported APIs. A retry over already-removed records reports `pass` with `already_removed` evidence instead of failing; a non-empty selection mismatch still raises so another document is never touched. Source deletion requires the expected document-scoped path and rejects symlinks/path mismatches.
5. SQL body clearing and final `pass` require all preceding required checks to pass. Failures preserve evidence/content needed for recovery and cannot become a green result. Artifact rows transition to `deleted_verified`/`residual_detected` on a best-effort basis; check rows are the source of truth.
6. The report is assembled from the persisted operation/check records. It establishes configured API-level accessibility only—not SSD, backups, journal, or universal physical erasure. Retry creates a new operation; earlier failures remain in history and are never overwritten.

## Fresh verification

`POST /api/v1/documents/{id}/verify` re-queries the real supported backends without deleting or changing anything: no unlink, no vector/cache/SQL writes, no artifact status changes. It persists a new `kind='verification'` operation with five read-only checks (source existence, Chroma residual read, cache residual read, retrieval probe when retained content exists, SQL re-read) plus any errors, using the same overall gate as deletion. A check that cannot run is `blocked`, never `pass`: for an already-deleted document the retrieval probe is inapplicable (content is gone by design), so a fully verified deletion reports `blocked` with four passing storage-state checks; for a still-indexed document the checks report presence as `fail`. Unknown documents return `not_verified` with no operation recorded. Verification history is append-only and never masks deletion history: dashboards and latest-deletion pointers consider `kind='deletion'` only.

Re-deleting an already-`deleted` document does not run new checks; it re-serves the completed deletion report annotated `served_from_history: true`. Fresh payloads carry `served_from_history: false`.

## Layer coverage

App-local implementation is distinct from external integrations. The local SQLite cache is an application-owned table, not a separate provider. There is no connector — and no coverage claim — for backups, replicas, provider retention/logs, external conversation memory, or vector-store vendors.

| Artifact / layer | Actual backing store | Operations supported | Deletion action | Independent verification method | Evidence persisted | Failure behavior | Restart persistence | Status |
|---|---|---|---|---|---|---|---|---|
| Source file | `backend/data/uploads/<doc>/<file>` | create (`O_EXCL`), unlink, exists | `Path.unlink` | Re-stat after unlink; prior-pass fingerprint match on retry | Path fingerprint, before/after existence, sanitized error | `fail`; SQL content retained | File + op/check rows persist | implemented and tested |
| ChromaDB chunk records | `PersistentClient` dir + collection `ghosterase_documents` | add / query / get / delete | `collection.delete(ids, where=document_id)` with exact-set guard | `collection.get(where=document_id)` residual read | Requested IDs, acknowledgement, residual ID list | `fail`, content retained; non-empty selection mismatch raises so other documents are never touched | Collection persists | implemented and tested |
| Application cache entries | SQLite `cache_entries` (same DB) | put / get / invalidate / residuals | `invalidated_at` timestamp | Residuals query for active rows | Invalidated keys, residual keys + count | Residuals → `fail`; adapter errors → `fail`/`blocked`; content retained | Rows persist | implemented and tested |
| Retrieval behavior | Chroma query path + SQL known-ID filter | query | None (probe, not deletion) | Head/middle/tail probes after delete | Matching chunk IDs, `probed_window_count` | Match → `fail`; query error → `blocked` | Recomputed per run | partial (query-path evidence only) |
| SQL metadata/content | SQLite `documents.content` | read / clear / re-read | Clear only after 5 prior checks pass | Re-read row after commit | `content_removed` flag | Write failure rolls back → `blocked`, content retained | Rows persist | implemented and tested (content only; identifiers retained) |
| Lineage pointers | SQLite `derived_artifacts` | create / backfill / status-mark | None (pointers, not data) | Cross-checked against checks; not independently verified | Stable IDs, parent links, statuses | Best-effort; never breaks orchestration | Rows persist | implemented and tested |
| Operations + reports | SQLite `deletion_operations` / `deletion_checks` | append / read | None | Report assembled from persisted rows | Full per-check evidence | Write failure surfaces with prior evidence kept | Rows persist (append-only) | implemented and tested |
| AI explanations | litellm, optional | explain | None (advisory) | Never authoritative; status echoed from report | Suggestions + dropped-step count | `unavailable`/`degraded` payloads, reports untouched | Computed per request, not stored | implemented and tested; one configured provider call verified live |
| Backups, replicas, provider retention/logs, external conversation memory, third-party caches, vector-store vendors | None (no connector) | None | None | None | None (`not_examined`) | n/a | n/a | unsupported |
| Hosted embeddings | External service | Config/client code only | n/a | Live embedding request returned HTTP 403; no embedding route or dimensions verified | n/a | Remains blocked; local ONNX is not a substitute | n/a | blocked |
| Neatlogs app trace | External observability | Metadata-only SDK spans | n/a | Synthetic GhostErase workflow trace visible in the project dashboard; spans contained no document text | n/a | Observability evidence only; never determines deletion status | n/a | verified |
| Entire checkpoint | Developer tooling | Not integrated | n/a | No real checkpoint captured | n/a | No provenance claim | n/a | unverified |
| CFO.ai read | Sponsor/business-planning tooling | Existing experimental OAuth/read-only panel | n/a | Live read succeeded, but the selected workspace had no pages or tables | n/a | Not part of target customer runtime; no financial conclusion | n/a | verified access; no data |

## Status semantics mapping

Required product vocabulary → code (`pass`/`fail`/`blocked`/`not_verified` per check; operation overall derived by one shared rule: all required checks pass → `pass`; any `fail` → `fail`; otherwise `blocked`):

| Required outcome | Code expression |
|---|---|
| Deletion requested | `POST …/delete` accepted (auth passed) + `status: running` operation row |
| Operation acknowledged | Per-check `api_response_received` / removal evidence (never sufficient alone) |
| Retrieval blocked | `application_retrieval` check `pass` (probe queries returned no target chunks) |
| Storage deletion verified | `source_file` / `chromadb_api` residual / `app_cache` / `sql_metadata` checks `pass` for that backend |
| Residual detected | Residual check `fail` with `residual_chunk_ids` / `residual_cache_keys` |
| Failed | Overall `fail` (any check failed); SQL content retained |
| Blocked | Overall `blocked` (a check could not run; e.g., backend outage, inapplicable probe); content retained |
| Unverified | `not_verified` (unknown document, unavailable metadata, or no operation exists) |
| Historical (not fresh) | `served_from_history: true` on a re-served deletion report; `kind: verification` operations are fresh evidence |

## AI investigation boundary

`POST /api/v1/deletion-operations/{id}/explain` returns advisory suggestions only (`backend/app/services/explainer.py`, `DocumentService.explain_operation`, `backend/app/main.py:explain_deletion_report`). The model receives privacy-minimized evidence (IDs, layers, methods, statuses, error categories, residual counts — never document text, embeddings, queries, secrets, or tokens) through the optional `ExplanationProvider` interface (`LiteLLMExplanationProvider`, 20 s timeout, temperature 0). Output is schema-validated; unrecognized or destructive recommendations are dropped and counted; malformed, slow, refused, or unconfigured providers return `degraded`/`unavailable` payloads without touching reports. The verified status is echoed from the persisted report and can never be changed by model output. Without `LITELLM_API_KEY` the endpoint reports unavailable and deterministic verification works unchanged. No mandatory paid dependency; nothing is sent externally by default.

## CFO.ai boundary
The backend uses the official MCP SDK OAuth provider, authorization code + S256 PKCE, the SDK's state/issuer checks, Streamable HTTP, and a loopback callback. Registration and tokens are stored in the OS credential vault, never returned to the frontend. Only discovered, schema-validated read tools (`list_orgs`, `inspect_pages`, `inspect_table_blocks`) are permitted. Financial responses remain in the process for the panel and are not put in the document index. Live verified 2026-10-11: real browser consent, dynamic registration, workspace `Yashas CFOAI` (103178), workspace-scoped `inspect_pages` read, and vault persistence across restart; session-context error mapping hardened with regression tests afterwards.

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

`id` primary key, `document_id` foreign key, JSON-encoded `chunk_ids`, `status`, `kind` (`deletion` or `verification`; backfilled as `deletion` on older databases), `created_at`, and nullable `completed_at`.

### `deletion_checks`

`id` primary key, operation/document foreign keys, JSON-encoded `chunk_ids`, `layer`, `method`, `status`, JSON-encoded `actual_result`, nullable sanitized `error_detail`, and `checked_at`.

### `derived_artifacts`

Current-state lineage pointers: stable `id` (`<doc>:source`, `<doc>:chunk:<chunk_id>`, `<doc>:sql`, `<doc>:cache:<key>`), `document_id` foreign key, `artifact_type` (`source_file`/`chroma_chunk`/`sql_content`/`app_cache_entry`), `backend` (`filesystem`, `chromadb:<collection>`, `sqlite`, `app_cache`), `identifier` (path/chunk ID/`documents.content`/cache key — IDs and hashes only, never raw text), nullable self-referencing `parent_id`, `status` (`active`/`deleted_verified`/`residual_detected`/`deletion_failed`), `created_at`, `updated_at`.

### `cache_entries`

Application-owned demo cache: `cache_key` primary key (`<query-sha256>:<document_id>`), `document_id` foreign key, `query_hash`, JSON-encoded `result_ids` (chunk IDs + distances), `created_at`, nullable `invalidated_at`. This table is GhostErase's own cache for the stale-cache demo; it is not a claim about third-party caches, which remain unverified.

## Database initialization and migration status

Importing `backend.app.db.session` calls shared `ensure_schema()`: `Base.metadata.create_all()` plus additive startup checks for the legacy `documents` columns and the `deletion_operations.kind` column. Connections also set `PRAGMA foreign_keys=ON`, so cross-table references (operations, checks, lineage, cache rows) are enforced at the database level; inconsistent writes fail loudly instead of creating orphans. The `derived_artifacts` and `cache_entries` tables are added the same way: `create_all` creates missing tables on existing databases without touching existing rows (covered by the migration test using a legacy-schema database file). These are **not versioned Alembic migrations**; no migration scripts or revision history are provided.

## Backup and recovery

Back up the local database before upgrades: copy `backend/data/ghosterase.sqlite3` (or your configured `DATABASE_URL` target) plus the `uploads/` and `chroma/` directories while the backend is stopped, and verify the copy opens before proceeding. Rollback is restoring those copies. Deletion history is append-only — retrying a failed operation adds a new operation and never rewrites the earlier failure.

Default local paths are `backend/data/ghosterase.sqlite3`, `backend/data/uploads`, and `backend/data/chroma`. E2E tests override all three to unique temporary directories and remove those paths after each run.
