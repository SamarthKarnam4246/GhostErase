# Feature status matrix

Status labels: `VERIFIED IN CODE AND TEST` | `IMPLEMENTED BUT NOT TESTED` | `PARTIAL` | `MISSING` | `BROKEN` | `UNVERIFIED`.
"Implemented but not tested" means: code read and verified in the 2026-10-10 audit, but **not rerun** on the audit machine (project deps and `frontend/node_modules` were absent). Historical pass claims live in [ACCEPTANCE.md](ACCEPTANCE.md) and are not current evidence. Full analysis: [AUDIT_2026-10-10.md](AUDIT_2026-10-10.md).

## Core RAG + deletion

| Feature | Status | Code | Evidence / limitation |
|---|---|---|---|
| `.txt`/`.md` upload validation (type, UTF-8, 5 MiB, non-empty) | IMPLEMENTED BUT NOT TESTED | `backend/app/main.py:243-265` | Tests exist (`backend/tests/unit/test_verification.py`); not rerun here |
| Deterministic chunking (1000/120) + `uuid5` chunk IDs | IMPLEMENTED BUT NOT TESTED | `backend/app/services/vector_store.py:27-49` | Deterministic, document-scoped lineage |
| Local ONNX embed + PersistentClient index/search | IMPLEMENTED BUT NOT TESTED | `backend/app/services/vector_store.py:16-24,51-76,118-148` | Local provider only; approved hosted route BLOCKED (see [ACCEPTANCE.md](ACCEPTANCE.md) §Core) |
| Source-file handling + traversal/symlink guards | IMPLEMENTED BUT NOT TESTED | `backend/app/services/document_service.py:42-49,339-412` | Path fingerprint is path identity, not content proof |
| 5-check deletion orchestration (source, vector delete, residual, retrieval, SQL clear) | IMPLEMENTED BUT NOT TESTED | `backend/app/services/document_service.py:339-552` | Failure-preserving; SQL cleared only if 4 priors pass |
| Persisted reports, honest scope (`physical_erasure_verified:false`) | IMPLEMENTED BUT NOT TESTED | `backend/app/services/document_service.py:554-629`, `frontend/src/App.tsx:368-392` | API-level accessibility only; see [ARCHITECTURE.md](ARCHITECTURE.md) |
| `DELETE_AUTH_TOKEN` via `X-Delete-Token`, fail-closed | IMPLEMENTED BUT NOT TESTED | `backend/app/services/document_service.py:293-302`, `backend/app/core/config.py:20` | Unset by default; constant-time compare |
| Dashboard aggregates from persisted rows | IMPLEMENTED BUT NOT TESTED | `backend/app/services/document_service.py:161-208` | No seeded figures |
| SQL content clearing | PARTIAL | `backend/app/services/document_service.py:498-507` | Content emptied + re-read, but filename/hash/chunk IDs/path retained — "cleared", not "nullified" |
| Retrieval-after-delete probe | PARTIAL | `backend/app/services/document_service.py:457-470` | Queries only first 1000 chars; tail-chunk coverage relies on residual check |
| `POST /verify` re-verification | MISSING | `backend/app/services/document_service.py:631-650` | Returns latest persisted report; executes no fresh checks. Frontend wrapper `frontend/src/api/client.ts:91-93` is dead (never imported in `App.tsx`) |
| Persistent cache layer + stale-cache demo | MISSING | — | No cache tables/service in code |
| Cross-provider lifecycle registry | MISSING | — | Only documents/ops/checks tracked; backups, replicas, memory, logs, exports listed as `not_examined` |
| AI investigation agent | MISSING | — | Deferred by plan decision; Neatlogs/CFO correctly decide nothing |
| Legacy `src/ghosterase` vector path | BROKEN (disabled by design) | `src/ghosterase/service.py`, `src/ghosterase/vectorstore.py` | Always `BLOCKED`; must not substitute for real path |

## Integrations

| Feature | Status | Code | Evidence / limitation |
|---|---|---|---|
| Neatlogs metadata-only telemetry + redaction | IMPLEMENTED BUT NOT TESTED | `backend/app/integrations/telemetry.py`, `backend/app/integrations/neatlogs.py` | Real app-trace readback UNVERIFIED; Doctor probe is SDK-only evidence |
| CFO.ai MCP OAuth read-only + vault fail-closed | IMPLEMENTED BUT NOT TESTED | `backend/app/integrations/cfo_ai.py`, `backend/app/main.py:202-240` | No live consent/workspace/read completed; allowlist `list_orgs`/`inspect_pages`/`inspect_table_blocks` |
| Approved hosted embeddings | BLOCKED | `backend/app/services/vector_store.py` | Separate audit reported HTTP 403 for `text-embedding-3-small`; dimensions unverified |
| Entire / Copilot CLI capture | BLOCKED | docs only | CLI not installed/on PATH; see [INTEGRATIONS.md](INTEGRATIONS.md) |

## Test / build reruns

| Command | Status on audit machine | Note |
|---|---|---|
| `PYTHONPATH=. python -m pytest -q` | UNVERIFIED (not runnable) | 5 collection errors: `fastapi`/`sqlalchemy`/`chromadb`/`dotenv`/`neatlogs` absent; system Python 3.9.6 < required `>=3.12` |
| `npm run build` / `npm run lint` | UNVERIFIED (not runnable) | `frontend/node_modules` absent |
| `npm run test:e2e` | UNVERIFIED (not runnable) | Requires install + Chromium + backend deps |
| `git diff --check` | Pass | Ran clean during audit |
