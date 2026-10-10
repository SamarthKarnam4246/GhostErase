# External integration setup and verification

GhostErase is local-first. Integration configuration or published protocol metadata is not proof of authenticated service access. The current verified/blocked states are recorded in `ACCEPTANCE.md` and `DEPENDENCY_AUDIT.md`.

## LiteLLM / hosted embeddings

The running RAG implementation uses Chroma's local `all-MiniLM-L6-v2` ONNX model. This is not the approved hosted embedding route.

- A separate MVP-session audit reports that the approved gateway returned HTTP 200 for small completion requests to Claude Sonnet 4.6 and Amazon Nova Lite, but HTTP 403 for the `text-embedding-3-small` request. No dimensions were verified; this worktree did not repeat those calls and did not receive the key.
- Do not place credentials in frontend code or commit them. If an authorized embedding endpoint becomes available, put its key only in local `.env`, perform the documented minimal embedding probe without logging key/body, validate returned dimensions, then implement the provider and rerun upload/persist/retrieve/restart before changing the acceptance status.
- Current direct OpenAI environment defaults are not a configured or approved gateway.
- There is no hosted-completion UI or generated report-summary endpoint in this build.

## Neatlogs runtime SDK

Backend instrumentation uses official Python SDK `neatlogs==1.4.26`, manual spans, no auto-instrumentation/log capture, and an export allowlist/redactor. The SDK endpoint is restricted to HTTPS hosts under `neatlogs.com`.

1. Create/identify the intended Neatlogs project and obtain its project key through the approved local secret-management path. Do not paste it into chat or commit it.
2. Set `NEATLOGS_API_KEY` in the ignored local `.env`. Set `NEATLOGS_ENDPOINT` to the official HTTPS ingest base URL for the correct region; do not use a URL containing credentials, query string, or fragment.
3. Restart the backend and confirm the status endpoint reports configuration, but treat `unverified` as unverified.
4. Run an actual synthetic app operation, then locate/read its trace in the intended project using supported Neatlogs tooling/dashboard. Inspect the actual exported trace for the synthetic marker and prohibited content.
5. Record sanitized result only. A local SDK Doctor, exporter unit test, or configured key does not establish remote delivery or readback.

The supplied project key is stored only in the ignored local `.env`; its file ACL is restricted to the current Windows user. The installed SDK is the latest published stable release (`1.4.26`). `python -m neatlogs doctor --probe --json` passed authenticated write, exact readback, and finalization for its generated four-span trace. This verifies the key-authenticated SDK path, but the probe output did not expose a project ID for independent comparison. The app itself was not run as requested, so no real GhostErase workflow trace or application-marker readback has been verified. Developer MCP access is not configured in this app.

For local verification use `python -m neatlogs doctor --local --json`. For authenticated SDK path verification, run `python -m neatlogs doctor --probe --json` from the backend's active Python environment after supplying `NEATLOGS_API_KEY` from an OS secret store or the ignored local `.env` to that process environment. Never place the key on the command line. Local mode proves only in-process SDK behavior; probe mode verifies its own generated trace through authenticated write/readback, not GhostErase's actual workflow. The app's Neatlogs status remains **unverified** until a synthetic GhostErase operation produces a finalized project trace and its exported fields are inspected for the marker and prohibited content.

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
3. Registration/tokens require the OS credential vault. On Windows, the expected backend is Windows Credential Manager. If the OS keyring is unavailable, the integration fails closed; no plaintext fallback exists.
4. Select only a workspace returned by live `list_orgs`. The app must complete a workspace-scoped `inspect_pages` read before marking the connection verified.
5. To load finance data, inspect the live `inspect_table_blocks` schema in the UI and submit arguments accepted by that schema. The server validates arguments and injects the selected workspace. Write/edit tools are rejected server-side.
6. If the response contains no relevant source evidence, the panel must retain `insufficient_financial_data`; it does not estimate deletion cost or legal exposure.
7. Disconnect clears local vault entries but does not claim to revoke the provider-side grant. Revoke that grant through CFO.ai account settings if necessary.

No consent, workspace, live read, or external financial evidence is currently available. Do not label this integration connected or verified until that flow succeeds.
