# Security

## Secrets and local configuration

- Keep all secrets in the local `.env` file only.
- `.env.example` contains variable names and non-secret defaults/placeholders only.
- Never commit real credentials, OAuth tokens, document payloads, embeddings, or uploaded user files.
- `.env`, SQLite files, upload directories, ChromaDB data, caches, and local integration state are ignored by Git. Verify with `git check-ignore -v .env` without printing the file contents.
- Set `DELETE_AUTH_TOKEN` locally before enabling deletion. The API compares it in constant time against the `X-Delete-Token` header; the default is blocked.
- Backend status endpoints expose configuration/verification states only; they do not return API keys, deletion tokens, OAuth access/refresh tokens, or client secrets.
- CFO.ai uses its interactive OAuth flow and OS credential vault. Do not put OAuth tokens in `.env`, the frontend, or logs. The UI's delete-token field is held in memory only and is never persisted to browser storage.
- A configured API key is not a verified credential. Use the integration's official minimal authenticated probe and report only sanitized outcomes.
- Do not store document text, filenames from untrusted paths, or credentials in deletion-operation/check evidence. Reports contain identifiers and sanitized results only.
- Deletion reports establish API-level accessibility only. They do not establish physical erasure from storage media, backups, filesystem journals, or external systems.

## Document and AI safety

- Accept only bounded UTF-8 `.txt`/`.md` uploads; generate document IDs and safe display filenames instead of using client-supplied paths.
- Resolve document source files beneath the configured application upload directory and reject symlink/path mismatches during deletion. Upload source files are created with owner-only POSIX modes where supported; Windows relies on the local directory's inherited ACLs.
- Document contents are untrusted data. They are rendered as text, not executable markup, and are not interpreted as instructions to the application. Do not pass them to a model as authority to invoke tools.
- Backend deletion statuses are computed only from persisted results of actual checks. No LLM or untrusted document content can mark checks as passed or initiate shell, SQL, infrastructure, or external write operations.
- CFO.ai is read-only through a server-side tool allowlist and live-schema validation. Financial data is not written to the document index; only the in-memory panel response is retained by this process.

## Network and deployment boundary

- The app rejects non-loopback client addresses and untrusted `Origin` headers, applies a loopback host allowlist, and adds `nosniff`, frame denial, no-referrer, and no-store response headers.
- Start the backend on `127.0.0.1` and use the Vite development proxy bound locally. Do not use `0.0.0.0`, a public reverse proxy/tunnel, or a shared deployment without a separately approved deployment/security plan.
- These controls are local-MVP guardrails, not a production security assessment. The project is not production-ready; do not expose it to untrusted networks or users.
- The app does not execute LLM-generated shell commands, SQL, infrastructure actions, or external financial writes.
- Do not paste secret values into README files, tests, or commit messages.
- Use the official SDK or CLI flow for each integration.
- Treat blocked external services as blocked, not as successful integrations.
