# Release checklist — local demo gate

Check each item with real commands. Do not check an item on the basis of a prompt, a screenshot, or a historical result.

## Clean install

- [ ] Fresh Python `>=3.12` venv created; `pip install -r requirements.txt` succeeds; `uv pip check` (or `pip check`) reports no broken requirements.
- [ ] `cd frontend && npm install` succeeds from the committed `package-lock.json` with no uncommitted manifest changes.
- [ ] `npx playwright install chromium` succeeds (one-time browser download).

## Environment setup

- [ ] `.env` created locally from `.env.example`; `DELETE_AUTH_TOKEN` set to a strong secret; `git check-ignore -v .env` confirms it is ignored; `git status` shows no secrets, databases, uploads, or model caches.
- [ ] Default data paths (`backend/data/`) absent or intentionally backed up before first run.

## Tests

- [ ] `PYTHONPATH=. .venv/bin/python -m pytest -q` green (latest recorded: 112 passed, 2 skipped).
- [ ] `cd frontend && npm run build && npm run lint` clean; `npm run test:unit` green (latest recorded: 26 passed).
- [ ] `PYTHON=<venv-python> npm run test:e2e` green (1 passed).
- [ ] `git diff --check` clean.

## UI build

- [ ] Production bundle builds (`tsc -b` + Vite); page title reads GhostErase; no mock values in production paths; statuses text-labeled.

## Authentication

- [ ] Deletion without a token returns 403 and records no operation; wrong token returns 403; correct token runs the workflow.

## Demo rehearsal

- [ ] `DELETE_AUTH_TOKEN=<secret> bash scripts/demo.sh` ends with `DEMO PASS` (upload → retrieval → lineage/cache → verified delete → no resurrection → residual leg → restart persistence → cleanup).

## Data cleanup

- [ ] Demo used isolated temp dirs only (script cleans up on exit); default `backend/data/` contains no demo or customer content; no temp dirs left behind.

## Secrets

- [ ] No secret values in source, frontend bundle, logs, reports, screenshots, or commits; canary phrases appear only as test markers with report-absence assertions.

## Persistence

- [ ] Restart rehearsal shows reports, dashboard counts, and lineage (`deleted_verified`) intact; backup is a stopped-copy of the SQLite file plus `uploads/` and `chroma/`.

## Known limitations (must ship with the release notes)

- [ ] Six configured checks only; `physical_erasure_verified: false`; `not_examined` list published.
- [ ] `POST /documents/{id}/verify` re-runs supported checks read-only and persists fresh evidence; historical report retrieval is a separate operation.
- [ ] Hosted embeddings remain blocked; Neatlogs synthetic app trace has dashboard evidence; Entire capture is not verified. CFO.ai read access was verified, but returned no financial evidence and is sponsor tooling rather than a target customer feature.
- [ ] `requirements.txt` and `backend/pyproject.toml` runtime/test dependencies are aligned; there are no versioned DB migrations.
- [ ] Loopback + synthetic data only; no public-deployment approval claimed.

## Integration compliance (separate gates, not this release)

- [ ] Neatlogs: real project-scoped trace run and read back (not the SDK probe alone).
- [ ] Entire: real checkpoint captured and listed.
- [ ] CFO.ai: browser OAuth completed, workspace selected, live read returned; IDs/links preserved.
- [ ] None marked complete without the above evidence.
