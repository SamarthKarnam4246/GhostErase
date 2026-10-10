# Phase 9 measurable acceptance record

This record reflects this worktree's independently rerun evidence on 2026-10-10. It does not adopt results reported by a different branch or session.

**Overall: NOT COMPLETE.** The local core workflow passes using ChromaDB's local `all-MiniLM-L6-v2` ONNX embedding function, but the approved hosted embedding route remains blocked (reported HTTP 403 in a separate authorized gateway audit; no embedding dimensions verified). Neatlogs' authenticated SDK probe passes, but the real application trace remains unverified; Entire capture and CFO.ai authorization/data access are also not verified.

Statuses:

- **PASS** — direct evidence in this worktree satisfies the stated criterion.
- **FAIL** — direct evidence demonstrates the criterion is not met.
- **BLOCKED** — an external prerequisite is unavailable, such as credentials, user authorization, or an uninstalled integration.
- **NOT VERIFIED** — implementation or local tests exist, but required direct evidence has not been collected.

## Core functionality

| # | Status | Evidence |
|---|---|---|
| 1 | PASS | `frontend/e2e/ghost-erase.spec.ts` launched a loopback Uvicorn backend and Vite frontend; Playwright passed. Startup host and proxy are local-only. |
| 2 | PASS | Browser E2E uploaded a per-run synthetic `.txt`; backend created the source file, SQL metadata, and Chroma chunks with document-scoped metadata. Integration test checks actual chunk IDs and metadata. |
| 3 | PASS | Playwright searched the unique marker in the real frontend through the application's `/api/v1/search` path before deletion. |
| 4 | PASS | Authorized real deletion ran the source unlink and Chroma collection deletion in the integration test and E2E. |
| 5 | PASS | Persisted verification report includes Chroma residual check via supported collection API; successful test observed no residual IDs. |
| 6 | PASS | After deletion, the application's search route returned no hit for the deleted document; the E2E checked its document ID explicitly. |
| 7 | PASS | A separate synthetic control remained retrievable before and after targeted deletion and after backend restart in the browser E2E. |
| 8 | PASS | E2E opened the UI report and checked `pass`; its persisted report contains five passing checks. Dashboard and report values are queried from persistent SQL records. |
| 9 | PASS | `test_failed_vector_delete_is_persisted_and_never_reports_success` injects a real-workflow Chroma delete failure; residual and retrieval checks fail, status is `fail`, and content remains for recovery. |
| 10 | PASS | Browser E2E stops and restarts the backend against the same isolated SQLite/upload/Chroma directories, then reads the deleted document, report, counts, and surviving control. |
| 11 | PASS | UI/report and integration assertions show `physical_erasure_verified: false`; SSDs, backups, journals, and unrelated storage are not claimed. |

**Scope note:** This establishes the local ONNX path only. It does not satisfy the separate approved-hosted-embedding provider requirement.

## Neatlogs

| # | Status | Evidence |
|---|---|---|
| 12 | NOT VERIFIED | The authenticated SDK Doctor probe reached and read back its finalized synthetic four-span trace. No real GhostErase operation was run or read back, so the application trace criterion remains unverified. |
| 13 | PASS (SDK probe only) | `python -m neatlogs doctor --probe --json` authenticated and read back the exact finalized probe trace. The probe did not expose a project ID, so the provided project ID was not independently compared; application workflow readback remains unverified. |
| 14 | NOT VERIFIED | The local masking-exporter test checks that synthetic marker content does not survive the export boundary. The actual app trace was not run or read back; Doctor probe content is generated test data, not the app's synthetic marker. |
| 15 | NOT VERIFIED | `test_neatlogs_trace_start_failure_is_not_reported_as_unverified_success` proves a synthetic trace-start failure is sanitized and visible as blocked. Remote exporter-delivery failure/readback was not exercised; SDK transport failure visibility is therefore not established end to end. |

## Entire and Copilot CLI

| # | Status | Evidence |
|---|---|---|
| 16 | BLOCKED | Copilot CLI 1.0.95 is installed, but `entire` is not installed/on `PATH`; no Entire enablement or repository capture status could be verified. Shared Git hooks were not changed. |
| 17 | BLOCKED | No Entire session/checkpoint was captured or inspected. The pushed Git commit is an ordinary Git commit, not evidence of Entire capture. |
| 18 | PASS | README, `SECURITY.md`, and dependency audit explicitly state that VS Code Copilot extension session capture is not claimed; documented support is for the CLI integration only. |

## CFO.ai

| # | Status | Evidence |
|---|---|---|
| 19 | NOT VERIFIED | Runtime code uses the official MCP SDK OAuth provider and Streamable HTTP, with PKCE and OS-vault credential storage. No actual OAuth registration/consent or authenticated MCP session was completed. |
| 20 | BLOCKED | No authorized CFO.ai browser consent/workspace is present; no workspace selection occurred. |
| 21 | BLOCKED | No real CFO.ai read-only operation/data response was obtained. |
| 22 | NOT VERIFIED | The panel renders the backend's returned table response and does not hardcode finance values, but no live authorized response has been demonstrated in the running UI. |
| 23 | PASS | Disconnected status is shown as unavailable in the E2E. `test_live_table_response_without_relevance_evidence_remains_insufficient` verifies that a synthetic read response stays `insufficient_financial_data` absent evidence of relevance; it does not claim a verified amount. |
| 24 | PASS | Server-side allowlist and schema-validation unit tests reject `edit_*` calls before invoking the fake client. No CFO write operation was performed. This is a local policy test, not live CFO.ai verification. |
| 25 | PASS | Code sends only allowed tool arguments, keeps OAuth records in the OS vault, and never indexes CFO results. Isolated read-only policy tests pass; no live financial payload was received or retained. |

## Quality and reproducibility

| # | Status | Evidence |
|---|---|---|
| 26 | NOT VERIFIED | `npm run build` passed (includes TypeScript `tsc -b`); `npm run lint` passed; `python -m pytest -q` passed. `git diff --check` passed, but no Python/TypeScript formatter is configured or installed, so formatting was not independently checked. |
| 27 | PASS | `python -m pytest -q` passed the integration tests using actual isolated ChromaDB PersistentClient collections and SQLite. The suite verifies ingestion, retrieval, deletion, residual checks, control preservation, persistence, and injected failure behavior. |
| 28 | PASS | Frontend production build passed with Vite after TypeScript build. |
| 29 | NOT VERIFIED | Tests cover invalid uploads, missing deletion authorization, Chroma query/delete failures, Neatlogs missing credentials and synthetic initialization/trace-start failures, CFO denied callback/workspace/schema/write/malformed response/timeout paths. The requested hosted-model missing/invalid-key, timeout/rate-limit, malformed generated-summary cases are not implementable/testable here because no hosted inference or summary path exists. Live Neatlogs/CFO negative cases also require authorized external connections. |
| 30 | NOT VERIFIED | README documents pinned backend/frontend installs, local environment setup, loopback startup, and test commands. Browser E2E uses isolated fresh storage, but no clean-machine dependency installation/reproduction run was made. The E2E requires Chromium via `npx playwright install chromium`. |

## Commands rerun for this record

```text
python -m pytest -q
39 passed, 3 dependency deprecation warnings

cd frontend
npm run build
passed (includes TypeScript type check)
npm run lint
passed
npm run test:e2e
1 passed (actual browser + local backend + PersistentClient Chroma; backend restarted)

python -m pip check
No broken requirements found.
python -m compileall -q backend
passed
git diff --check
passed
```

The separate external-service and provider completion gates remain unresolved. Do not report GhostErase as fully complete or production-ready.
