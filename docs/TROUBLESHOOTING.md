# Troubleshooting

## Backend does not start

- Use a Python version supported by all pinned packages (`requirements.txt`; project metadata requires Python >=3.12). This worktree was tested with Python 3.13.14.
- From the repository root, install dependencies with `python -m pip install -r requirements.txt`.
- Start with `python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000`. Do not bind to `0.0.0.0`; remote clients are intentionally rejected.
- Port 8000 may be in use. Choose an unused loopback port and update `frontend/vite.config.ts` or `GHOSTERASE_API_PROXY` accordingly.
- The Vite proxy calls `127.0.0.1:8000` by default. A frontend that loads but reports backend unavailable commonly has a mismatched proxy target or stopped backend.
- Host/Origin allowlists permit local hostnames only. Do not work around them with a public tunnel or remote browser.

## Upload, embedding, or search fails

- Upload accepts `.txt`/`.md`, text content types, non-empty valid UTF-8, and a maximum 5 MiB. Errors do not echo file contents.
- First Chroma ONNX use may need to obtain its model artifact and may fail if unavailable. Check the sanitized API response and local Chroma diagnostics. Do not report indexing success when the operation fails.
- The current provider is Chroma's local `all-MiniLM-L6-v2` ONNX embedding function. The approved hosted embedding route is separate and remains blocked; do not substitute a direct OpenAI endpoint or claim it is the approved gateway.
- Verify the Chroma collection path and writable local data directory. The default is `backend/data/chroma`; do not point tests at a production/user dataset.

## Deletion is blocked or fails

- Deletion is blocked until `.env` contains a strong `DELETE_AUTH_TOKEN` and the UI sends its exact value in memory via `X-Delete-Token`.
- A failed source/vector/retrieval/metadata check should remain visible in the report. Inspect the operation's individual persisted checks; do not treat a Chroma delete acknowledgement alone as verified deletion.
- A source path mismatch, symbolic link, or unexpected document-scoped path is deliberately rejected. Do not manually change operation evidence to force success.
- A partial operation may require the service's retry path. The original failed checks and failure status remain in the audit records.
- Reports cover configured application APIs only. They do not prove physical storage erasure, backups, filesystem journals, or unrelated systems.

## Run automated tests

From repository root:

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
Push-Location frontend
npm ci
npx playwright install chromium
npm run build
npm run lint
npm run test:e2e
Pop-Location
```

Playwright E2E creates its own uniquely named local database, upload directory, and Chroma store under ignored `frontend/.playwright-data`, starts loopback-only backend/frontend processes, and cleans up in `finally`. `frontend/test-results` is also ignored. If a test process is interrupted, inspect the specific ignored directory before cleanup; do not point tests at the default application data.

## Neatlogs status is blocked/unverified

- Missing key is expected to report blocked. Add a project key only through local secret handling, then restart.
- A key's presence is not proof it is valid. An invalid key, unsupported region URL, or failed span start should show a sanitized error category.
- Run `python -m neatlogs doctor --local --json` for network-free SDK diagnostics and `python -m neatlogs doctor --probe --json` for authenticated SDK probe write/readback. The probe is synthetic SDK evidence, not a real GhostErase workflow trace.
- The synthetic exporter test proves the local export redaction boundary. It is not evidence that a real application trace is private. Use the intended project's supported trace readback/dashboard to inspect an app trace before claiming that gate.

## Entire/Copilot integration unavailable

- Copilot CLI may be installed while `entire` is absent. Check the official installation instructions and installed-release help before acting.
- This worktree's Git hooks path is shared across worktrees. Do not enable Entire until hook impacts and checkpoint/transcript privacy are understood.
- A regular Git commit is not an Entire checkpoint. Do not create fake checkpoint files.

## CFO.ai is not connected or reports insufficient evidence

- OAuth requires interactive browser consent and a working loopback callback on port 8765.
- The runtime requires a supported OS credential vault. If the vault is unavailable or locked, no plaintext storage fallback is provided.
- Workspace IDs must come from live `list_orgs`; arbitrary IDs are rejected.
- The actual `inspect_table_blocks` input schema is shown after live tool discovery. Do not guess unsupported arguments.
- A verified workspace does not imply relevant financial data. An empty/malformed/irrelevant response must not be interpreted as deletion cost or legal exposure.
- Unit tests with fake MCP tools are isolated policy tests, not CFO.ai authentication or service-availability evidence.

## External integrations or acceptance gates

See [`ACCEPTANCE.md`](ACCEPTANCE.md) for the per-criterion status and evidence. Neatlogs readback, Entire capture, CFO.ai live data, and the approved hosted embedding route remain blocked/unverified. The project is not production-ready.
