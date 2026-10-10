# AGENTS.md — GhostErase Contributor Guidance

Durable instructions for coding sessions. Derived from the read-only audit (`docs/AUDIT_2026-10-10.md`) and verified against the actual code. No secrets, credentials, document bodies, or fabricated claims in this file.

## 1. What GhostErase is

Security and data-governance product for RAG applications. It tracks a document's known data lineage, executes authorized deletion/invalidation against supported storage layers, independently verifies each operation with recorded evidence, and reports precisely what the evidence supports. Differentiator: lifecycle tracking + evidence-backed verification. It is not a file manager or a "Deleted" button.

Source of truth for product rules: the project briefing; truthfulness rules: never confuse requested / acknowledged / retrieval-blocked / storage-verified / unverified / residual-detected. See `docs/AUDIT_2026-10-10.md` §5.

## 2. Verified architecture (do not assume beyond this)

- Backend: Python + FastAPI, entry `backend/app/main.py` (`app`, `service = DocumentService()`, `cfo_ai = CfoAiIntegration()`). Loopback-only + Origin allowlist + security headers (`main.py:72-103`).
- Services: `backend/app/services/document_service.py` (`DocumentService`: ingest, search filter, 5-check deletion orchestration, report assembly, dashboard aggregates); `backend/app/services/vector_store.py` (`ChromaVectorStore`: `PersistentClient`, `DefaultEmbeddingFunction` = `all-MiniLM-L6-v2` ONNX, deterministic `chunk_text` 1000/120 and `uuid5` chunk IDs).
- Persistence: SQLite via `backend/app/db/session.py` (`create_all` + inline additive `ALTER TABLE`, **no Alembic revisions**); tables `documents`, `deletion_operations`, `deletion_checks` (`backend/app/models/`). Defaults: `backend/data/ghosterase.sqlite3`, `backend/data/uploads`, `backend/data/chroma` (`backend/app/core/config.py:14-22`). E2E/tests override to temp dirs.
- Auth: `DELETE_AUTH_TOKEN` env (unset by default) vs `X-Delete-Token` header, `secrets.compare_digest` (`document_service.py:293-302`); blocked before operation creation.
- Frontend: React + TS, `frontend/src/App.tsx` screens `overview|documents|reports|integrations`, API wrapper `frontend/src/api/client.ts`, types `frontend/src/types.ts`, Vite `/api` proxy to `127.0.0.1:8000`.
- Integrations: Neatlogs metadata-only spans with export allowlist (`backend/app/integrations/telemetry.py`); CFO.ai MCP OAuth read-only allowlist + OS-vault fail-closed (`backend/app/integrations/cfo_ai.py`). Neither decides deletion status. No cache layer, no lifecycle registry beyond documents/ops/checks, no AI agent exist yet.
- Legacy `src/ghosterase/` is disabled-by-design (`BLOCKED`); never re-enable as a substitute vector path.
- Full data flow, schema, and migration notes: `docs/ARCHITECTURE.md`. Acceptance record: `docs/ACCEPTANCE.md`. Audit: `docs/AUDIT_2026-10-10.md`. Status matrix: `docs/feature-status.md`. Phases: `docs/roadmap.md`.

Note: this repo uses UPPERCASE doc names (`ARCHITECTURE.md`, not `architecture.md`). On case-insensitive filesystems they collide — do not create lowercase duplicates.

## 3. Commands that really work

These are the documented commands, copied from `README.md` (Local setup + Validation). Rerun status on the audit machine: **not yet rerunnable** — project Python deps and `frontend/node_modules` were absent, `.env` and `backend/data/` absent (see audit §8). P0 is to rerun them and record actuals.

```bash
python -m pip install -r requirements.txt
cd frontend && npm install
# copy .env.example to .env locally; set DELETE_AUTH_TOKEN to a strong local secret (never commit)
PYTHONPATH=. python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
cd frontend && npm run dev -- --host 127.0.0.1 --port 5173
```

```bash
PYTHONPATH=. python -m pytest -q
cd frontend && npm run build && npm run lint
npx playwright install chromium && npm run test:e2e
```

Rules: Python `>=3.12` (per `backend/pyproject.toml`); backend binds `127.0.0.1` only; tests use isolated temp dirs — never point them at production data; first Chroma ONNX run may download the model. Known drift: `requirements.txt` vs `backend/pyproject.toml` differ (`alembic` vs `numpy/pytest/httpx/litellm`, `httpx` vs `httpx2`) — reconcile in P0, don't paper over it.

## 4. Security rules (from `SECURITY.md`, binding)

- Secrets only in local ignored `.env`; never in source, frontend, logs, reports, commits, screenshots. Verify ignore with `git check-ignore -v .env` without printing contents.
- `DELETE_AUTH_TOKEN` unset = deletion blocked. Compare in constant time; token held in UI memory only, never browser storage.
- Uploads: bounded `.txt`/`.md`, text content types, valid UTF-8, 5 MiB cap; safe display filenames; document-scoped paths; reject symlinks/mismatches; restrictive POSIX modes where supported.
- Document contents are untrusted data: render as text, never as instructions; never send bodies/canaries to telemetry or CFO.ai. Telemetry exports allowlisted metadata only.
- SQLAlchemy ORM only; no raw-SQL string interpolation. Errors sanitized (no content/credential echo).
- Local-MVP network posture only: loopback clients, trusted origins, `nosniff/DENY/no-referrer/no-store`. Never serve publicly without a separately approved plan. Not production-assessed.

## 5. Coding conventions

- Smallest coherent diffs; follow existing patterns (`DocumentService`, `ChromaVectorStore`, `_record_check` evidence shape); no rewrites or formatting churn.
- Typed signatures, Pydantic validation at API boundary, `limit` bounds enforced.
- Every deletion-related outcome persisted with timestamp, method, identifiers, result, sanitized error. Partial failure is first-class; SQL content retained unless all prior checks pass.
- No real component replaced with mock/fake/in-memory/hard-coded success to make builds pass. Unsupported layers report `unsupported`/`unverified`.
- No success, integration, deployment, or security guarantee claimed without executed evidence.

## 6. Test requirements

- `pytest.ini` markers: `unit` (isolated; fakes are not integration proof), `integration` (real local PersistentClient/SQLite), `external` (authorized service only, never mocked).
- Cover failure paths as seriously as happy paths (auth rejection, Chroma failure injection, oversized/invalid uploads, residual detection, control preservation, restart persistence).
- Historical counts (39 pytest + 1 E2E) are not current evidence — rerun and record exact commands + outputs every phase.
- Frontend: `tsc -b` via `npm run build`, `oxlint` via `npm run lint`, Playwright E2E on isolated dirs with cleanup.

## 7. Phase discipline

Work in the `docs/roadmap.md` phases (P0 → P3), one phase at a time. End every phase with: changes, tests actually run + results, limitations, changed files, remaining work. Stop at the boundary. Never fabricate green statuses or connector responses.
