# GhostErase

## Product

GhostErase is an AI/data privacy lifecycle assurance platform. Its initial wedge is evidence-backed privacy-action verification for RAG and AI pipelines, where source data can propagate into chunks, vectors, caches, generated text, logs, and other artifacts. The distinctive loop is known artifact lineage, fresh connector-specific verification, residual investigation, and an evidence report that states its exact scope and limits.

The architecture can grow to represent other data resources and privacy actions, but the local MVP executes deletion only against real implemented stores. Unknown systems are coverage gaps, not imaginary resources or successful checks. GhostErase does not claim universal discovery or erasure.

## Initial users

RAG engineers and privacy/security reviewers of a **local** prototype who need evidence-backed answers to "what happened to this data in the configured systems, what can be verified now, and what remains unknown?" GhostErase is a local MVP, not a production service.

## What it does

GhostErase tracks a document's known lineage (source file → chunks → vector records → cache entries → metadata/report rows), executes authorized deletion across its supported adapters, verifies each layer with real backend checks, and reports exactly what the evidence supports — including partial failures, residuals, and unverified layers.

## What it is not

Not a universal deletion product. It does not prove secure physical erasure, SSD remanence clearing, backup/replica removal, or clearance of systems it cannot inspect. Reports say `physical_erasure_verified: false` and list what was not examined.

## Architecture

Python/FastAPI backend + SQLite metadata/audit store + ChromaDB `PersistentClient` with the local `all-MiniLM-L6-v2` ONNX embedding function + React/TypeScript SPA (Vite dev proxy to the backend). Details, data flow, schema, and migration notes: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Supported artifact layers

| Layer | Check | Evidence |
|---|---|---|
| Source file | `source_file` unlink + existence re-check | Path fingerprint, before/after existence |
| Vector records | `chromadb_api` delete (exact-ID guard) + residual `get` | Requested IDs, acknowledgement, residual ID list |
| Application cache | `app_cache` invalidate + residual query | Invalidated keys, residual key list |
| App retrieval | `application_retrieval` head/middle/tail probes | Matching chunk IDs, probed window count |
| SQL metadata | `sql_metadata` clear-then-reread (gated on 5 prior passes) | Content-removed flag |
| Advisory explanations | `POST …/explain` (optional litellm) | Suggestions only; never changes status |

Explicitly **unsupported/unverified**: SSD remanence, filesystem journals, backups, replicas, provider retention, conversation memory, logs, exports, and third-party caches. Hosted embeddings remain blocked; Entire capture is not verified. A synthetic Neatlogs app trace was visible in the project dashboard, and CFO.ai read access was verified but returned no financial data. CFO.ai remains sponsor tooling, not a core product integration.

## Deletion and report semantics

- `blocked` — missing/invalid `DELETE_AUTH_TOKEN` (`X-Delete-Token` header); no operation created.
- `not_verified` — unknown document, or the metadata store could not be inspected; nothing recorded.
- `pass` — all six configured checks passed. Overall operation `pass`; document marked `deleted`.
- `fail` — a check failed or a residual was found; SQL content retained for recovery; retry safe.
- `blocked` (operation) — a check could not run (e.g., backend unavailable); content retained.
- Acknowledgement ≠ verification. A missing search hit proves only that query path is blocked. Retries append new operations; earlier failures stay in history. Reports are assembled from persisted rows before they are returned.
- `POST /documents/{id}/verify` re-queries the live backends read-only and persists a new verification record without deleting anything. A deleted document verifies as `blocked` with four passing storage checks (no content remains to probe retrieval with); re-deleting one re-serves its deletion report flagged `served_from_history`.

## Local setup (macOS/Linux)

Prerequisites: Python `>=3.12`, Node 18+, free loopback ports 8000/5173 (8765 for the optional CFO.ai callback).

```bash
python3.13 -m venv .venv 2>/dev/null || python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # or: uv pip install --python .venv/bin/python -r requirements.txt
cd frontend && npm install && cd ..
cp .env.example .env   # then set DELETE_AUTH_TOKEN to a strong local secret; never commit .env
```

`requirements.txt` installs the complete local development/test environment. `backend/pyproject.toml` declares the same runtime dependencies and provides a `test` extra for pytest and the FastAPI test client. The database uses additive schema initialization; it does not currently have versioned Alembic migrations.

Run the backend (loopback only — never `0.0.0.0` without a separately approved plan):

```bash
PYTHONPATH=. .venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Run the frontend from a second terminal:

```bash
cd frontend && npm run dev -- --host 127.0.0.1 --port 5173
```

## Configuration

All secrets live in the ignored local `.env` (see [.env.example](.env.example); verify with `git check-ignore -v .env` without printing contents):

| Variable | Purpose | Default |
|---|---|---|
| `DELETE_AUTH_TOKEN` | Deletion authorization; unset = deletion blocked | unset |
| `DATABASE_URL` | SQLite store | `backend/data/ghosterase.sqlite3` |
| `GHOSTERASE_UPLOAD_DIR` / `CHROMA_DIR` | Source files / vector store | `backend/data/uploads`, `backend/data/chroma` |
| `NEATLOGS_API_KEY` / `NEATLOGS_ENDPOINT` | Optional telemetry (metadata only) | unset / `https://ingest.neatlogs.com` |
| `LITELLM_API_KEY` / `LITELLM_API_BASE` / `LITELLM_MODEL` | Optional advisory explanations | unset / configured LiteLLM gateway / `openai/claude-haiku` |

## Running tests

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q          # 112 tests: units + real-ChromaDB/SQLite integration + failure injection
cd frontend && npm run build && npm run lint        # tsc + Vite + oxlint
cd frontend && npm run test:unit                    # 26 vitest component tests
npx playwright install chromium                     # one-time browser download
cd frontend && PYTHON="$(cd .. && pwd)/.venv/bin/python" npm run test:e2e   # 1 Chromium E2E; absolute path is needed because the backend starts from the repo root
```

Notes: tests use isolated temp directories and must never point at production data. The first Chroma run downloads the ONNX model into the local cache. E2E needs loopback ports and the backend venv. Current results: 112 + 26 + build/lint clean + 1 E2E passed (see [docs/feature-status.md](docs/feature-status.md)).

## Synthetic demo workflow

Synthetic data only. The runnable script performs the live legs; the stale-cache leg runs as a deterministic integration test (the app exposes no test hooks to plant stale rows, by design):

```bash
bash scripts/demo.sh   # needs: curl, jq, python3, the .venv backend deps, DELETE_AUTH_TOKEN set
```

1. Starts an isolated backend (temp DB/uploads/Chroma) and uploads a document with a random per-run canary phrase.
2. Confirms it is indexed and retrievable; confirms a cache entry exists via the lineage endpoint.
3. Deletes with `X-Delete-Token`; asserts the report is `pass` with six passing checks.
4. Confirms post-delete searches stay empty (cache does not resurrect content).
5. Runs the stale-cache residual case (`test_cache_residual_is_detected_then_remediated_on_retry`): first report `fail` with `residual_cache_keys`, content retained; retry after remediation `pass`; history shows `[fail, pass]`.
6. Restarts the backend against the same isolated dirs; asserts the report, dashboard counts, and lineage (all `deleted_verified`) persist. Cleans up temp dirs.

You can repeat the same steps manually in the UI (Documents → upload/search/delete, Reports → evidence, restart backend, reload). Never paste canary values into external logs; the script keeps them in local variables only.

## Troubleshooting

- Backend won't start: use Python `>=3.12`; install from `requirements.txt`; bind `127.0.0.1` (remote clients are rejected by design); check the port is free.
- Upload/search fails: only `.txt`/`.md` with text content types, valid UTF-8, non-empty, ≤ 5 MiB. First Chroma run needs network for the model artifact.
- Deletion blocked: set `DELETE_AUTH_TOKEN` in `.env` and enter the exact value in the UI token field (memory-only); 403 means missing/mismatched token.
- A `fail` report is evidence, not a bug: open the operation, read the failing check's method/result/error, remediate (e.g., invalidate the residual), retry — history is preserved.
- Full table: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Current limits

- Six configured checks only; everything else is `not_examined` and reported as such.
- `POST /documents/{id}/verify` re-runs supported checks read-only and persists a new verification operation. Historical report retrieval is separate.
- Hosted embedding route blocked (HTTP 403 reported for `text-embedding-3-small`; dimensions unverified) — local ONNX does not substitute for it.
- Neatlogs: a synthetic GhostErase workflow trace was verified in the project dashboard and showed metadata-only spans. Entire: no real capture is verified. CFO.ai OAuth/read was verified, but the workspace had no financial data; CFO.ai is sponsor tooling, not a customer-facing product capability. The existing runtime panel is slated for removal as the UI becomes a privacy-operations workspace.
- No versioned DB migrations (additive `create_all`; back up `backend/data/` before upgrades). Not assessed for production; loopback + synthetic data only.

## Optional development and sponsor tools (not core product features)

Neatlogs is limited to privacy-filtered runtime observability. Entire is development provenance. CFO.ai is for business planning, not normal customer runtime. Their status must remain bounded to the evidence recorded in [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) and [docs/feature-status.md](docs/feature-status.md); none determines deletion status. AWS and Crustdata are not runtime dependencies.

## Documentation map

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, flows, schema, migration, backup/recovery, AI boundary
- [docs/feature-status.md](docs/feature-status.md) — capability matrix with code evidence + current test results
- [docs/roadmap.md](docs/roadmap.md) — P0 → P3 phases
- [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) — historical acceptance record (30 criteria)
- [docs/AUDIT_2026-10-10.md](docs/AUDIT_2026-10-10.md) — read-only audit
- [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md), [docs/DEPENDENCY_AUDIT.md](docs/DEPENDENCY_AUDIT.md), [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md), [docs/release-checklist.md](docs/release-checklist.md)
- [SECURITY.md](SECURITY.md) — controls, review scope, pre-public requirements
- [AGENTS.md](AGENTS.md) — contributor guidance

## Guardrails

- `.env`, databases, uploads, Chroma data, caches, `node_modules`, `.venv`, and test artifacts are Git-ignored; no secrets, documents, or databases are committed.
- Never report embedding/indexing/retrieval/deletion success without exercising the real provider and persistent workflow.
- Uploaded text is untrusted data rendered as text; no model output or document content decides deletion status or runs shell/SQL/infrastructure/external writes.
- Local MVP safeguards only — no production security assessment, no public deployment.
