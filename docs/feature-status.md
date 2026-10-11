# Feature status matrix

Status labels: `VERIFIED IN CODE AND TEST` | `IMPLEMENTED BUT NOT TESTED` | `PARTIAL` | `MISSING` | `BROKEN` | `UNVERIFIED`.
Historical pass claims live in [ACCEPTANCE.md](ACCEPTANCE.md). Full analysis: [AUDIT_2026-10-10.md](AUDIT_2026-10-10.md).
Rerun history on the macOS dev machine (venv Python 3.13.12): P3 — 43 passed + E2E 1 passed; P4 — 49 passed + E2E 1 passed; P5 — 57 passed + E2E 1 passed; P8 — 74 passed; F1–F5 review fixes — 83 passed; lifecycle/verify phase — 93 passed; stale-cache acceptance phase — 96 passed; security hardening — 100 passed; test-strategy phase — 105 passed; integrations verification — 108 passed + E2E 1 passed, `npm run build`/`npm run lint` clean; CFO session fix — 110 passed, 2 skipped; review-findings fixes (E2E vault precondition, kind label, doc corrections) — 110 passed, 2 skipped; full sweep (registry wiring, malformed cache rows, file-picker reset, disabled-button polish) — 112 passed, 2 skipped.

## Core RAG + deletion

| Feature | Status | Code | Evidence / limitation |
|---|---|---|---|
| `.txt`/`.md` upload validation (type, UTF-8, 5 MiB, non-empty) | VERIFIED IN CODE AND TEST | `backend/app/main.py:upload_document`, `backend/tests/unit/test_verification.py` | Invalid-type/UTF-8/oversize cases covered |
| Deterministic chunking (1000/120) + `uuid5` chunk IDs | VERIFIED IN CODE AND TEST | `backend/app/services/vector_store.py:chunk_text`, `_chunk_id` | Deterministic, document-scoped lineage |
| Local ONNX embed + PersistentClient index/search | VERIFIED IN CODE AND TEST | `backend/app/services/vector_store.py` | Local provider only; approved hosted route BLOCKED (see [ACCEPTANCE.md](ACCEPTANCE.md) §Core) |
| Source-file handling + traversal/symlink guards | VERIFIED IN CODE AND TEST | `DocumentService._safe_filename`, `_delete_document` source stage | Path fingerprint is path identity, not content proof |
| 6-check deletion orchestration (source, vector delete, residual, cache, retrieval, SQL clear) | VERIFIED IN CODE AND TEST | `DocumentService._delete_document` | Failure-preserving; SQL cleared only if 5 priors pass; `pass` needs all 6 |
| Persisted reports, honest scope (`physical_erasure_verified:false`) | VERIFIED IN CODE AND TEST | `DocumentService.deletion_report`, `frontend/src/App.tsx` reports screen | API-level accessibility only; see [ARCHITECTURE.md](ARCHITECTURE.md) |
| `DELETE_AUTH_TOKEN` via `X-Delete-Token`, fail-closed | VERIFIED IN CODE AND TEST | `DocumentService._delete_document` auth gate | Unset by default; constant-time compare; rejected before operation creation |
| Dashboard aggregates from persisted rows | VERIFIED IN CODE AND TEST | `DocumentService.dashboard_data` | No seeded figures |
| App lineage registry (`derived_artifacts`: source/chunk/cache/sql pointers) | VERIFIED IN CODE AND TEST | `backend/app/models/lineage.py`, `DocumentService._ensure_lineage`, `backend/tests/unit/test_lineage_cache.py` | Current-state pointers with stable IDs; evidence stays in append-only checks. In-app artifacts only |
| Application-owned demo cache + adapter (`app_cache` check) | VERIFIED IN CODE AND TEST | `backend/app/services/cache_store.py`, `backend/app/services/adapters.py`, `backend/tests/unit/test_lineage_cache.py` | Query hashes + result IDs only, never raw text. Stale entries fail verification; third-party caches stay unverified |
| Lineage detail API (`GET /documents/{id}/lineage`) | VERIFIED IN CODE AND TEST | `DocumentService.document_detail`, `backend/app/main.py:document_lineage` | Read-only; artifacts + cache entries + unknown-copies note; 404 for unknown IDs |
| Security-product UI (badges, lineage view, derived indicators, advisory panel) | VERIFIED IN CODE AND TEST | `frontend/src/App.tsx`, `frontend/src/components/Status.tsx`, `frontend/src/App.css` | 19 vitest component tests + E2E green; statuses always text-labeled; global live region; responsive + focus-visible; no mock values in production paths |
| Vector retry-safety (`already_removed` evidence) | VERIFIED IN CODE AND TEST | `ChromaVectorStore.delete_chunks` | Empty-selection + empty-residuals reports pass; non-empty mismatch still raises so other documents are never touched |
| SQL content clearing | PARTIAL | `DocumentService` metadata stage | Content emptied + re-read, but filename/hash/chunk IDs/path retained — "cleared", not "nullified". Write failures roll back and record `blocked` |
| Retrieval-after-delete probe | PARTIAL | `DocumentService._retrieval_probe_windows` | Head/middle/tail probes (`probed_window_count` in evidence); still query-path evidence only |
| `POST /verify` fresh verification | VERIFIED IN CODE AND TEST | `DocumentService._verify_current_state`, `backend/tests/unit/test_verify_fresh.py` | Re-queries all supported backends read-only; persists `kind='verification'` ops; never mutates artifacts; retrieval probe inapplicable without retained content (→ `blocked`); unknown docs `not_verified` with no op. Re-delete re-serves history flagged `served_from_history` |
| Cross-provider/external lifecycle (backups, replicas, memory, logs, exports) | MISSING | — | Listed as `not_examined`; no imaginary connectors built |
| AI investigation assistant (advisory only) | VERIFIED IN CODE AND TEST | `backend/app/services/explainer.py`, `POST .../explain`, `backend/tests/unit/test_explainer.py`, reports-screen advisory panel | Live `ok` verified via gateway `openai/claude-haiku` mapping (fenced-JSON tolerated); provider credentials selected by model prefix, fail-closed; groq blocked (no usable model for supplied key); unsafe steps dropped; status invariant tested; privacy-minimized evidence only |
| Legacy `src/ghosterase` vector path | BROKEN (disabled by design) | `src/ghosterase/service.py`, `src/ghosterase/vectorstore.py` | Always `BLOCKED`; must not substitute for real path |

## Integrations

| Feature | Status | Code | Evidence / limitation |
|---|---|---|---|
| Neatlogs metadata-only telemetry + redaction | VERIFIED IN CODE AND TEST | `backend/app/integrations/telemetry.py` | Unit/redaction tests pass; live app trace renders exact span tree in project dashboard with empty span I/O and no document text in tree/timeline/graph views; Doctor probe is SDK-only evidence |
| CFO.ai MCP OAuth read-only + vault fail-closed | VERIFIED IN CODE AND TEST | `backend/app/integrations/cfo_ai.py` | Live verified 2026-10-11: browser consent, dynamic registration, workspace `Yashas CFOAI` (103178), `inspect_pages` read, vault persistence across restart. Workspace empty → financial impact `insufficient_financial_data` by rule. Refreshed-session schema errors are unwrapped to mapped 4xx with sanitized detail (unit-tested; live re-proof in that exact session state still needs restart + re-consent) |
| Approved hosted embeddings | BLOCKED | `backend/app/services/vector_store.py` | Separate audit reported HTTP 403 for `text-embedding-3-small`; dimensions unverified |
| Entire / Copilot CLI capture | BLOCKED | docs only | CLI not installed/on PATH; see [INTEGRATIONS.md](INTEGRATIONS.md) |

## Test / build reruns (current)

| Command | Result | Note |
|---|---|---|
| `PYTHONPATH=. .venv/bin/python -m pytest -q` | 112 passed, 2 skipped | Python 3.13.12 venv; third-party warnings only (anyio/litellm/pydantic deprecations, none from repo code). Adds registry-wiring + malformed-cache-row units |
| `cd frontend && npm run build && npm run lint` | Pass / 0 warnings, 0 errors | `tsc -b` + Vite + oxlint |
| `cd frontend && npm run test:unit` | 26 passed | vitest + jsdom + testing-library; badges, indicators, limitation texts, theme/progress/failure-guidance/freshness/unsupported-label/file-picker-reset states |
| `PYTHON=.venv/bin/python npm run test:e2e` | 1 passed | `PYTHON` env required: bare `python` is not on PATH here. Covers upload → retrieval → delete → pass → cache non-resurrection → restart persistence (report, dashboard, lineage). The stale-cache residual leg runs at integration level (failure injection; no prod test hooks) |
| `git diff --check` | Pass | |
