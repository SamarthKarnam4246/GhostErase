# External integration setup and verification

GhostErase is local-first. Integration configuration or published protocol metadata is not proof of authenticated service access. The current verified/blocked states are recorded in `ACCEPTANCE.md` and `DEPENDENCY_AUDIT.md`.

## Current integration status

- **Neatlogs:** a real synthetic GhostErase workflow trace was visible in the intended project dashboard; the inspected span tree showed no document text. This proves metadata-only workflow trace delivery for that observation, not deletion correctness.
- **CFO.ai:** live browser consent, workspace discovery, and a read-only workspace-scoped read succeeded. The workspace had no pages or tables, so financial impact remains `insufficient_financial_data`. CFO.ai is sponsor/business-planning tooling, not a target customer runtime feature.
- **Entire:** no authentic checkpoint/capture is verified.
- **Hosted embeddings:** blocked; the tested embedding request returned HTTP 403 and the gateway did not advertise an embedding model.

## LiteLLM / hosted embeddings and completions

The running RAG implementation uses Chroma's local `all-MiniLM-L6-v2` ONNX model. This is not the approved hosted embedding route.

- On 2026-10-11 a user-supplied gateway key (ignored local `.env` only, minimal spend) verified live: `/models` lists 7 chat models (`claude-haiku`, `claude-opus-4.6`, `claude-sonnet-4.6`, `nova-2-lite`, `nova-lite`, `nova-micro`, `nova-pro`) and no embedding models; a tiny `claude-haiku` completion succeeded both directly and through litellm with the `openai/` provider prefix (bare model names are rejected client-side by litellm); `text-embedding-3-small` returned live HTTP 403. Code defaults now point at this gateway with model `openai/claude-haiku`, and the advisory explainer path returned live `ok` against it. Hosted embeddings remain blocked.
- A separate earlier MVP-session audit reported HTTP 200 completions and the same HTTP 403 for embeddings on this gateway; those results are now superseded by the live re-verification above for completions.
- Do not place credentials in frontend code or commit them. The current `LITELLM_API_BASE` default is the user-provided gateway, not direct OpenAI.
- A `GROQ_API_KEY` may be configured for future `groq/`-prefixed explainer models (credential selected by model prefix, fail-closed when absent), but live probes show the supplied key authenticates yet has no usable model (`llama-3.3-70b-versatile` and `llama-3.1-8b-instant` report model-not-found, `gemma2-9b-it` is decommissioned, `/models` returns 403). Groq stays blocked until an exact available model is confirmed; do not switch defaults to it.
- There is no hosted-completion UI or generated report-summary endpoint in this build.

## Neatlogs runtime SDK

Backend instrumentation uses official Python SDK `neatlogs==1.4.26`, manual spans, no auto-instrumentation/log capture, and an export allowlist/redactor. The SDK endpoint is restricted to HTTPS hosts under `neatlogs.com`.

1. Create/identify the intended Neatlogs project and obtain its project key through the approved local secret-management path. Do not paste it into chat or commit it.
2. Set `NEATLOGS_API_KEY` in the ignored local `.env`. Set `NEATLOGS_ENDPOINT` to the official HTTPS ingest base URL for the correct region; do not use a URL containing credentials, query string, or fragment.
3. Restart the backend and confirm the status endpoint reports configuration, but treat `unverified` as unverified.
4. Run an actual synthetic app operation, then locate/read its trace in the intended project using supported Neatlogs tooling/dashboard. Inspect the actual exported trace for the synthetic marker and prohibited content.
5. Record sanitized result only. A local SDK Doctor, exporter unit test, or configured key does not establish remote delivery or readback.

The supplied project key is stored only in the ignored local `.env`; its file ACL is restricted to the current Windows user. The installed SDK is the latest published stable release (`1.4.26`). `python -m neatlogs doctor --probe --json` passed authenticated write, exact readback, and finalization for its generated four-span trace. This verifies the key-authenticated SDK path, but the probe output did not expose a project ID for independent comparison. This was an intermediate status before the subsequent dashboard verification described below.

Live re-check on 2026-10-11 with a user-supplied key: the authenticated probe failed twice with `TRACE_READBACK_TIMEOUT`, and SDK delivery diagnostics report upload authority unavailable for the key with zero export failures — treat the key as unauthorized for upload (or mismatched project/region) until the owner confirms. The `.env` value was later hash-compared against the instructed key (lengths and SHA-256 match; values never displayed) and diagnostics re-run: still unauthorized, so mistyping is ruled out. A real synthetic workflow was run against a backend configured with the key (upload → retrieval → verified delete → fresh verification; clean shutdown; no SDK errors in backend output). Per-trace readback remains unverified: the CLI only fetches by trace ID, the app does not surface trace IDs, and `trace get` additionally requires a separate `NEATLOGS_TOKEN` service-account credential that was not supplied. Status stays **unverified**.

CLI readback findings on 2026-10-11 (`neatlogs-cli` 0.2.0 and 0.2.1, device login approved 3×, macOS keychain profile `setup-https---app-neatlogs-com`): `projects list` confirms project `2b57fd04-…` ("Yashas's project", default, project_admin); `whoami` confirmed the same project binding with `observability:read` scope. `traces list` works intermittently (omit `--json`, which breaks it in these CLI versions; `--limit` values are unreliable; most failures are server-side flakiness with mislabeled errors). `traces get` and `traces spans list` never succeeded, so CLI span-level readback is unavailable — use the dashboard instead. Attribution of older `neatlogs-app` traces is unknown (teammate or organizer activity possible on the shared project).

Dashboard readback on 2026-10-11 (human-verified screenshots, strongest evidence): the project trace list shows a real GhostErase deletion trace rendering our exact span tree — `deletion-verification` WORKFLOW root → `deletion.orchestration` CHAIN → `deletion.source_removal` TOOL, `deletion.vector_removal` TOOL, `deletion.residual_check` RETRIEVAL, `rag.vector_query` RETRIEVAL, `deletion.metadata_clear` TOOL, and `deletion.verification_check` CHAINs with per-step timings (14 steps, 0s). Delivery of app workflow traces to the intended project is therefore proven; the earlier `upload_authority_available: false` diagnostic did not mean spans never land. Two fresh 11:59 traces were still finalizing at read time, and one 11:16 trace shows a platform-side "processing failed" state (server-side, not app evidence). The opened span inspector shows an empty I/O panel ("No input or output recorded for this span") with no document text anywhere in the tree, timeline, or graph views — consistent with the allowlisted exporter boundary proven by unit tests, which permit only IDs, counts, layer names, and result codes. Verdict: **verified** — real app trace delivered to the intended project with metadata-only content throughout the visible surface. This is observability evidence only, not deletion evidence.

For local verification use `python -m neatlogs doctor --local --json`. For authenticated SDK path verification, run `python -m neatlogs doctor --probe --json` from the backend's active Python environment after supplying `NEATLOGS_API_KEY` from an OS secret store or the ignored local `.env` to that process environment. Never place the key on the command line. Local mode proves only in-process SDK behavior; probe mode verifies its own generated trace through authenticated write/readback, not GhostErase's actual workflow. Current GhostErase trace delivery is verified by the dashboard observation above; repeat the synthetic workflow/readback after material instrumentation changes.

## Entire with GitHub Copilot CLI

Entire is development provenance, not the deletion runtime. Copilot CLI 1.0.95 is installed here; Entire is not installed/on `PATH`, so no Entire command, auth, enablement, status, or checkpoint verification was performed.

When setting up a future development environment:

1. Read the current official Entire CLI install/Copilot CLI integration docs: [Entire CLI](https://entire.io/gh/entireio/cli) and [Copilot CLI integration](https://docs.entire.io/agents/copilot-cli).
2. Inspect the installed Entire version's supported commands and current repository/hook configuration before enabling it. Here `core.hooksPath` resolves to a hooks directory shared across worktrees; enabling hooks without understanding the impact could alter other checkouts.
3. Review session/checkpoint contents and storage/sync behavior. Agent prompts/tool calls and shadow working-tree snapshots can contain sensitive material; redaction is not guaranteed. Use synthetic documents and never include credentials.
4. Follow the installed release's official enable flow, start an actual supported Copilot CLI session, make a real development change and commit, then inspect actual status/checkpoint through supported commands.
5. Do not fabricate checkpoint files. No claim is made that Entire tracks the VS Code Copilot extension; only CLI support is documented.

## CFO.ai runtime

The code uses the official MCP SDK OAuth provider and `https://api.cfo.ai/mcp`; protocol metadata was discovered, but no authenticated user authorization or model read has been performed.

To verify with an authorized account:

1. Run GhostErase locally and open the Integrations panel.
2. Select **Connect to CFO.ai**. The backend initiates OAuth and listens on `127.0.0.1:8765/callback`; complete consent in the browser. OAuth state/issuer validation is handled by the official MCP SDK.
3. Registration/tokens require the OS credential vault (macOS Keychain on the verified host). If the OS keyring is unavailable, the integration fails closed; no plaintext fallback exists.
4. Select only a workspace returned by live `list_orgs`. The app must complete a workspace-scoped `inspect_pages` read before marking the connection verified.
5. To load finance data, inspect the live `inspect_table_blocks` schema in the UI and submit arguments accepted by that schema. The server validates arguments and injects the selected workspace. Write/edit tools are rejected server-side.
6. If the response contains no relevant source evidence, the panel must retain `insufficient_financial_data`; it does not estimate deletion cost or legal exposure.
7. Disconnect clears local vault entries but does not claim to revoke the provider-side grant. Revoke that grant through CFO.ai account settings if necessary.

The following live verification supersedes the earlier pre-consent status. Do not infer financial evidence from a successful connection or empty workspace.

Live verification on 2026-10-11 (this worktree, macOS keychain vault): real browser consent completed, dynamic registration issued live client IDs, `list_orgs` returned one workspace (`Yashas CFOAI`, ID `103178`, auto-selected), and a workspace-scoped `inspect_pages` read succeeded — connection `verified`. That workspace holds 0 pages and 0 tables, so the panel correctly stays `insufficient_financial_data`. Vault entries survived a backend restart (workspace ID restored without browser), but the restarted session required full re-consent — silent refresh did not re-verify. A refreshed-session schema error is now mapped to a sanitized 4xx and covered by unit tests; an exact live re-proof after restart and re-consent remains outstanding.
