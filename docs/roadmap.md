# Roadmap — P0 → P3

The current implementation is a local MVP, not the full product target. The architecture target is privacy lifecycle assurance, initially focused on evidence-backed deletion in RAG pipelines; later phases must add capabilities incrementally. Work one phase at a time and stop at each boundary with changes, tests actually run, results, limitations, changed files, and remaining work. Never fabricate green statuses.

## P0 — Safe baseline (no app changes)

- Keep user changes and Git checkpoints intact; never treat an existing staged worktree as disposable.
- Keep `requirements.txt` as the complete local dev/test install and `backend/pyproject.toml` as runtime metadata plus the matching `test` extra. Do not declare Alembic until versioned migration scripts are adopted; current schema evolution is additive.
- Use an ignored local `.env`; never print or commit its contents.
- Baseline rerun on 2026-10-11: backend `112 passed, 2 skipped`; frontend build/lint clean and unit tests `26 passed`; Chromium E2E `1 passed`; isolated synthetic demo `DEMO PASS`; project venv `uv pip check` clean; compileall and whitespace checks clean.
- P0 gate: manifest parity and current behavioral documentation must be verified before continuing. Do not count historic rows in `feature-status.md` as current evidence.

## P1 — Lifecycle registry + persistent cache + verification hardening

Status: implemented and covered by the current backend suite; see `docs/feature-status.md`.

- Add SQLite `derived_artifacts` (IDs/hashes only, never raw text) and `cache_entries` tables; `PersistentCacheService` (put/get/invalidate/residuals); record lineage at ingest/cache time.
- Adapter split (`SourceFile`/`Chroma`/`Cache`/`SqlContent`); orchestrator gains cache check (6 checks), partial-failure + safe retry preserved; broaden retrieval probe beyond first 1000 chars; document SQL as "content cleared, identifiers retained".
- `POST /documents/{id}/verify` re-executes **read-only** checks and persists a new verification operation (`kind='verification'`); `verifyDocument` is wired to a UI "Verify now" button; re-delete of an already-deleted document re-serves history flagged `served_from_history` (implemented and tested; the earlier report-retrieval design is superseded).
- Unsupported layers (backups, replicas, retention, conversation memory, logs, exports) stay `unsupported`/`unverified`, never simulated.
- Accept: stale-cache test fails-then-passes; all evidence persisted; no canary text in telemetry.

## P2 — Reports, coverage UI, signature demo

Status: reports and coverage UI are implemented. The full demo still needs one in-process residual-failure → authorized remediation → fresh re-verification path; the current script runs the residual acceptance case as a separate test.

- Extend `deletion_report` scope with per-layer methods, cache evidence, explicit unsupported list.
- UI: lifecycle view, per-check evidence cards, supported-vs-unverified coverage panel, plain-language blocked/unverified/failed states, inline token guidance.
- Demo (synthetic only, E2E + manual script): canary upload → retrieval + cache entry → stale-cache delete shows residual/failure → remediate → re-verify passes with backups unverified → restart persistence confirmed.
- Accept: E2E stale-cache branch green; manual script reproducible from docs.

## P3 — Hygiene and close-out

- Isolate/remove legacy `src/ghosterase` dead path only after P0–P2 green; document the no-Alembic migration strategy; document `httpx` (FastAPI test client) versus `httpx2` (CFO.ai OAuth client).
- Full rerun (pytest + build + lint + E2E + `pip check` + `compileall` + `git diff --check`); update [ACCEPTANCE.md](ACCEPTANCE.md), `IMPLEMENTATION_REPORT.md`, and [feature-status.md](feature-status.md) with actual outputs.
- Hosted embeddings remain blocked and Entire capture remains unverified. Neatlogs trace delivery and CFO.ai read access are verified only for the evidence and limitations in `docs/feature-status.md`; CFO.ai remains sponsor tooling.
- Accept: no historical claims presented as current; limitations explicitly listed.

## Beyond the local MVP

After the P0–P3 local-MVP gates, continue in the order defined by the product architecture:

1. **Two bounded agents:** separate privacy-action planning from residual investigation. Both use typed tools and structured outputs; neither authorizes destructive work, executes arbitrary actions, or changes verification status. Core deletion remains usable without a model provider.
2. **Privacy-operations workspace:** prioritize work needing attention and coverage gaps; add provenance-aware lineage exploration, scope review/approval, evidence-linked investigation, before/after reports, history, and export. Remove CFO.ai sponsor tooling from the normal customer workflow.
3. **Continuous privacy regression:** add metadata-only lineage instrumentation, repeatable safe read-only probes, supported-store orphan scans, stale-cache/access-propagation tests, freshness and failure alerts, and a documented manual/CI command only after destructive safeguards are reliable.
4. **Subject workflow and one connector:** add match provenance and human review for ambiguous identities, policy/hold/approval states, then choose a single real connector from user evidence and test its success, failure, timeout, unsupported, and authorization behavior.
5. **Additional privacy actions:** implement access/export, correction, restriction/permission revocation, consent/opt-out propagation, or retention expiry one at a time. Each action needs its own policy, authorization, idempotency, connector capability, and verification contract.
6. **Production readiness when justified:** threat model, tenant isolation, identity/RBAC, durable work, versioned migrations, secret management, deployment controls, recovery exercise, monitoring, and independent review.
7. **Product expansion and validation:** maintain dated competitive evidence, interview target buyers, validate a design partner and economic model, and choose new domains based on repeated customer pain.

Backups, replicas, processor internals, learned model influence, unknown offline copies, consumer broker opt-outs, and unrelated incident-response/security functions remain out of scope unless separately validated and supported by real mechanisms. Keep all claims bounded to inspected systems and evidence.
