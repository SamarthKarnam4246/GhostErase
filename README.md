# GhostErase

GhostErase is a local document-indexing and deletion-orchestration prototype. Its deletion report covers configured application APIs only; it does not prove physical media erasure.

## Included foundation

- FastAPI backend, SQLAlchemy metadata and operation evidence store
- Persistent ChromaDB integration using its real `DefaultEmbeddingFunction` (`all-MiniLM-L6-v2` ONNX); this local provider is not the approved hosted embedding provider required for Phase 2 completion
- React + TypeScript UI for `.txt`/`.md` upload, retrieval, deletion, and reports
- Versioned local documentation and environment template
- Git ignore rules for local credentials, documents, and generated caches

## Current implementation status

- Upload code validates `.txt`/`.md` type, UTF-8 content, and a 5 MiB limit, writes a unique source path, chunks deterministically, and calls ChromaDB's local ONNX embedding function. A prior local environment reported a successful synthetic upload/retrieval/restart/retrieval run; that historical result is not a current-runtime gate result and does not satisfy the approved-provider requirement.
- Deletion requires a configured `DELETE_AUTH_TOKEN` supplied in the `X-Delete-Token` request header. Without it, deletion is blocked before an operation is created.
- Authorized deletion persists an operation and individual timestamped checks, then removes the source file, deletes only recorded chunk IDs for the target document, queries ChromaDB for residual IDs, and runs a post-delete retrieval query. SQLite content is cleared only after all four preceding checks pass; on failure it is retained for recovery and the operation cannot pass.
- Operation reports are assembled from persisted records and retain failed, blocked, and unverified results. `pass` requires all five configured checks to pass.
- The legacy `src/ghosterase` synthetic-vector path remains disabled; it is not the API implementation and cannot create substitute vectors.
- Neatlogs uses the official Python SDK with manual metadata-only spans and strict export-boundary redaction. Local SDK diagnostics and synthetic-marker exporter tests pass, but external delivery/readback is blocked without the project's key. A separate MVP-session audit reports that Claude Sonnet 4.6 and Amazon Nova Lite completion routes returned HTTP 200 through the approved gateway, while its `text-embedding-3-small` request returned HTTP 403; hosted embedding dimensions remain unverified. This worktree did not repeat those remote calls or receive the key. CFO.ai now has an implemented official-MCP-SDK OAuth flow and read-only panel, but no dynamic client registration, browser consent, workspace selection, or financial-model read has yet been completed. Entire status belongs in documentation only.

## Verification limits

- Chroma's documented default embedding function can use the real `all-MiniLM-L6-v2` ONNX model. A prior local execution exercised it, but mere configuration or that separate historical test does not establish current runtime operation. The first run may download the model into Chroma's user cache; subsequent runs may reuse it.
- **Phase 2 completion gate remains blocked.** The approved hosted embedding route returned HTTP 403 in a separate MVP-session audit; embedding dimensions were not validated. No current-runtime upload → persistent chunks → retrieval → backend restart → retrieval gate is claimed. The local ONNX function does not substitute for the approved provider.
- Reports are limited to API-level accessibility in the configured source-file path, ChromaDB collection, normal retrieval query, and SQLite metadata. A missing search result is evidence only for that query path.
- SSD remanence, backups, filesystem journals, and unrelated storage are not examined. Physical erasure is always reported as unverified.
- `DELETE_AUTH_TOKEN` is unset by default. Set a local secret in `.env`; never commit it or expose it in frontend source.
- Neatlogs status is shown from backend configuration and remains unverified until an authenticated app trace is read back from the intended project. CFO.ai runtime auth is implemented but remains unverified until a user completes browser OAuth, workspace selection, and the actual read gate. No hosted-provider calls are made until the approved gateway/model routes are configured.
- CFO.ai panel uses the documented endpoint and official MCP SDK with authorization-code OAuth, PKCE, SDK state/issuer checks, a fixed loopback callback and Windows Credential Manager. It discovers actual tools and allows only `list_orgs`, `inspect_pages`, and `inspect_table_blocks`; no `edit_*` tool can be invoked through GhostErase. No provider credentials or financial values are stored in browser storage or source. The integration remains **unverified** until a real user authorizes, selects the intended workspace, and completes an actual read-only inspection.
- A verified CFO.ai connection is not proof of financial impact. The UI explicitly reports **insufficient financial data** until a real table response is loaded; it does not estimate deletion costs or legal exposure.

## Phase 7 dashboard and reporting

- The Overview reads document, deletion-operation, and individual-check totals from the configured SQL database. It shows persisted status breakdowns and recent operation links; values are not seeded demo figures.
- Document management displays the generated document ID, safe filename, creation/indexing time where recorded, current indexing/deletion state, chunk count, and available verify/delete actions. Earlier database rows without a creation timestamp remain explicitly “Not recorded.”
- Deletion reports display every recorded check, its timestamp, method, status, result, identifiers, and sanitized error when present, with storage/API scope and physical-erasure limitations. A report is not described as secure physical deletion.
- Integration states come from Neatlogs configuration/SDK status and CFO.ai's runtime state. Neatlogs developer MCP is shown as not configured; Entire/Copilot CLI is shown as not configured for the runtime because it is development provenance, not an application service. Neither is presented as connected or verified.
- The CFO.ai panel renders only a response from its live authenticated read-only integration. A disconnected account shows financial data as unavailable; a verified workspace without relevant table evidence shows insufficient data. No sample production figures are supplied.

## Local setup

The following commands run the implemented local application:
1. Create and activate a virtual environment using a Python version supported by all pinned dependencies (this worktree was checked with Python 3.13.14; Python 3.12 is not installed in the current environment).
2. Install backend dependencies:
   ```bash
   python -m pip install -r requirements.txt
   ```
3. Install frontend dependencies:
   ```bash
   cd frontend
   npm install
   ```
4. Populate a local `.env` from `.env.example`; set `DELETE_AUTH_TOKEN` to a strong local secret. Do not check in the resulting `.env`.
5. Start the backend:
   ```bash
   cd ..
   PYTHONPATH=. python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
   ```
6. Start the frontend from a second terminal:
   ```bash
   cd frontend
   npm run dev -- --host 127.0.0.1 --port 5173
   ```

## Validation commands

```bash
PYTHONPATH=. python -m pytest -q
cd frontend
npm run build
npm run lint
npx playwright install chromium
npm run test:e2e
```

The deletion integration tests use unique synthetic markers and the real ChromaDB API/model, with an unrelated control document and an injected deletion failure case. They verify application retrieval before and after deletion, persisted checks, and the control document's continued availability.

## Phase 9 automated testing

- `python -m pytest -q` includes isolated `unit` tests and `integration` tests that use actual PersistentClient ChromaDB collections and isolated temporary SQLite/upload/vector directories. The Chroma tests exercise upload, application retrieval, real deletion, control-document preservation, failure injection, API status/error behavior, report serialization, and persistence after service reconstruction.
- `cd frontend && npm run test:e2e` runs Playwright Chromium against an actual loopback Uvicorn backend and Vite frontend. It creates an isolated temporary database/upload/Chroma directory, uploads a per-run synthetic marker, retrieves it, deletes it, confirms an unrelated control remains retrievable, opens and checks the displayed persisted verification report, restarts the backend process, and confirms document/operation/check state and control retrieval persist. Test-only files are removed in `finally`; never point these tests at production data. This runs with Chroma's local ONNX embedding function and does not satisfy the separate approved hosted-provider Phase 2 gate.
- `unit` tests using in-memory fake credential or MCP clients test parsing, schema enforcement, callback validation, sanitized failures and write-tool denial only. They are not external CFO.ai verification.
- Neatlogs tests cover missing credentials, local export-boundary redaction, and sanitized initialization/trace-start failures with a fake SDK. No authenticated Neatlogs trace/readback is currently possible without authorized project credentials and a supported readback path.
- Entire/Copilot CLI tests are unavailable because neither CLI is installed in this environment. No mock checkpoint is counted.
- CFO.ai tests use fakes only for isolated read-only policy, parsing, and error logic. No user OAuth consent or authorized workspace is present, so live authentication/read access is unverified.
- Hosted model calls, grounded-generation summaries, and their credential/timeout/rate-limit/malformed-summary cases are not implemented in this worktree. No hosted-model failure is disguised as successful output; the active embedding path is local Chroma ONNX and remains distinct from the blocked approved hosted embedding route.

## Important guardrails

- `.env` and all local data directories are ignored by Git.
- `.env.example` is versioned and contains variable names plus non-secret defaults/placeholders only.
- No secrets, uploaded documents, or local database files are committed.
- Do not report successful embedding, indexing, retrieval, or deletion verification unless the real provider and persistent ChromaDB workflow have been exercised successfully.
- Neatlogs local diagnostics are not proof of remote connectivity. Configure `NEATLOGS_API_KEY` and the correct region base URL only through a local secret store; then run the documented authenticated probe and verify a real GhostErase trace in the intended project. Do not put keys in frontend code or commit them.
- Neatlogs telemetry drops logs and exports only allowlisted operational metadata. It does not capture prompts, queries, chunks, document content, model outputs, financial data, credentials, span events, or exception descriptions. Do not enable automatic instrumentation without an independent privacy review.
- Hosted completions were verified only in a separate MVP-session audit, not from this worktree. Hosted embeddings are blocked (HTTP 403 reported for `text-embedding-3-small`), and no embedding dimensions are verified. Do not implement or claim that hosted RAG path or run its gate until the authorized embedding route works. The current direct OpenAI configuration is not the approved gateway.
- Phase 8 local-only controls reject non-loopback clients and untrusted request origins; run the backend on `127.0.0.1`, not a public interface. Uploads receive restrictive POSIX file/directory modes where available, and Windows uses inherited local directory ACLs.
- Uploaded text is treated as untrusted data and rendered as text. No model output or document content controls deletion status or initiates shell, SQL, infrastructure, or external write operations.
- `.env`, integration credentials, database files, uploaded documents, Chroma data, and caches remain ignored. Verify `.env` ignore status without displaying its content using `git check-ignore -v .env`.
- These safeguards apply to a local MVP only. GhostErase has not undergone a production security assessment and is not approved for public deployment.

## CFO.ai OAuth / financial panel

- The unauthenticated MCP challenge returned HTTP 401 and a protected-resource metadata URL identifying the authorization server. This establishes only that the endpoint is protected and publishes metadata, not that GhostErase is authorized.
- The application uses the official `mcp==2.3.0` SDK `OAuthClientProvider` with authorization-code flow, S256 PKCE, SDK state/issuer validation, and Streamable HTTP. It declares `http://127.0.0.1:8765/callback`; the callback server binds only to loopback, does not log query strings, and never returns authorization codes to the frontend. Dynamic client registration and tokens are persisted only with `keyring==25.7.0`'s Windows Credential Manager backend. The integration fails closed if a supported OS vault is unavailable.
- Live OAuth metadata advertises the registration endpoint and S256 PKCE. The client uses SDK metadata discovery and registration instead of assuming the server accepts the fixed callback. Registration, browser approval, workspace discovery, and an authorized read have not yet been performed. Registration failures return sanitized status only; raw response bodies are withheld.
- The financial panel only permits live-discovered `list_orgs`, `inspect_pages`, and `inspect_table_blocks` read operations. Server-side JSON Schema validation and selected-workspace injection prevent selection of any `edit_*` operation. Document text, chunks, embeddings, and deletion markers are not sent to CFO.ai.
- Disconnect removes local client-registration and token records from the OS vault; it does not claim to revoke the CFO.ai-side OAuth grant. Revoke the app authorization in CFO.ai account settings if server-side revocation is required.
- Connection status is verified only after authentication, live tool discovery, actual `list_orgs`, workspace selection (when needed), and a successful workspace-scoped `inspect_pages` call. A verified connection still leaves financial impact **insufficient** until actual `inspect_table_blocks` data is loaded. The panel displays server-returned data and tool provenance, but does not infer deletion cost or legal exposure.
- The CFO.ai integration is implemented but its live completion gate remains pending because no user has completed browser authorization. No credentials were copied or requested in chat.

## Entire / Copilot CLI provenance status

- Git 2.53.0.windows.4 is available, but neither the Entire CLI nor Copilot CLI is installed; their versions and authentication cannot be checked.
- Current Entire documentation lists a native Copilot CLI integration (`copilot-cli`) that writes `.github/hooks/entire.json`. That documents support for Copilot CLI, not for the VS Code Copilot extension; no VS Code capture claim is made.
- This worktree has no commits and no Entire settings or checkpoint. No hook setup or synthetic checkpoint was created. Git resolves hooks to the shared `C:\Users\samar\.git\hooks` directory, so running `entire enable` here could modify hooks shared with other worktrees; setup was intentionally not attempted.
- Entire's official storage guidance says checkpoints are stored in Git refs by default, local until pushed, and linked to a real commit. Session data may include prompts, transcripts, tool calls, token usage, and files-touched metadata. The security guide warns that redaction is not guaranteed and shadow branches can contain raw working-tree snapshots; review these storage and privacy implications before enabling capture or pushing checkpoint refs.
- Phase 5 completion gate is **unverified / blocked**. To verify it later, install and authenticate the official CLIs, inspect the actual Entire version and `copilot-cli` agent status, review the shared-hook impact, start a Copilot CLI session from this repository, make a real development change and commit, then inspect `entire status --detailed` and `entire checkpoint list`. Do not claim Entire captures VS Code Copilot sessions unless official support is documented.
