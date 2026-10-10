# Roadmap — P0 → P3

Plan decisions already taken: SQLite cache table; `POST /verify` re-executes read-only checks; AI layer deferred; demo via E2E + manual script. Work one phase at a time, stop at each boundary with changes / tests run + results / limitations / changed files / remaining work. Never fabricate green statuses.

## P0 — Safe baseline (no app changes)

- Create Python `>=3.12` venv; `python -m pip install -r requirements.txt`; `pip check`.
- Reconcile `requirements.txt` vs `backend/pyproject.toml` drift (`alembic` vs `numpy/pytest/httpx/litellm`; `httpx` vs `httpx2`).
- Rerun and record exact outputs: `PYTHONPATH=. python -m pytest -q`; `cd frontend && npm run build && npm run lint`; `npx playwright install chromium && npm run test:e2e`.
- Set local `.env` (`DELETE_AUTH_TOKEN`, never commit).
- Accept: actual pass/fail counts recorded; failures become P0 bugs. Status matrix updated from historical to current evidence.

## P1 — Lifecycle registry + persistent cache + verification hardening

- Add SQLite `derived_artifacts` (IDs/hashes only, never raw text) and `cache_entries` tables; `PersistentCacheService` (put/get/invalidate/residuals); record lineage at ingest/cache time.
- Adapter split (`SourceFile`/`Chroma`/`Cache`/`SqlContent`); orchestrator gains cache check (6 checks), partial-failure + safe retry preserved; broaden retrieval probe beyond first 1000 chars; document SQL as "content cleared, identifiers retained".
- Rework `POST /documents/{id}/verify` to re-execute **read-only** checks and persist a new verification operation; wire dead `verifyDocument` wrapper to a UI Re-verify button.
- Unsupported layers (backups, replicas, retention, conversation memory, logs, exports) stay `unsupported`/`unverified`, never simulated.
- Accept: stale-cache test fails-then-passes; all evidence persisted; no canary text in telemetry.

## P2 — Reports, coverage UI, signature demo

- Extend `deletion_report` scope with per-layer methods, cache evidence, explicit unsupported list.
- UI: lifecycle view, per-check evidence cards, supported-vs-unverified coverage panel, plain-language blocked/unverified/failed states, inline token guidance.
- Demo (synthetic only, E2E + manual script): canary upload → retrieval + cache entry → stale-cache delete shows residual/failure → remediate → re-verify passes with backups unverified → restart persistence confirmed.
- Accept: E2E stale-cache branch green; manual script reproducible from docs.

## P3 — Hygiene and close-out

- Isolate/remove legacy `src/ghosterase` dead path only after P0–P2 green; document Alembic decision; resolve `httpx`/`httpx2` duplication.
- Full rerun (pytest + build + lint + E2E + `pip check` + `compileall` + `git diff --check`); update [ACCEPTANCE.md](ACCEPTANCE.md), `IMPLEMENTATION_REPORT.md`, and [feature-status.md](feature-status.md) with actual outputs.
- Hosted embeddings, Neatlogs live trace, CFO live auth, Entire capture remain `blocked`/`unverified` until their documented live gates pass.
- Accept: no historical claims presented as current; limitations explicitly listed.
