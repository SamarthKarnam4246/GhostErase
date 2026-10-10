from __future__ import annotations

import asyncio
import json
import platform
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import parse_qs, urlsplit

import httpx2
import keyring
from jsonschema.exceptions import SchemaError, ValidationError as JSONSchemaValidationError
from jsonschema import validate as validate_json_schema
from mcp import Client
from mcp.client.auth import (
    AuthorizationCodeResult,
    OAuthClientProvider,
    OAuthFlowError,
    OAuthRegistrationError,
    OAuthTokenError,
)
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl, ValidationError

from backend.app.core.config import settings

CFO_SERVER_URL = "https://api.cfo.ai/mcp"
CFO_REDIRECT_URI = "http://127.0.0.1:8765/callback"
CFO_CALLBACK_HOST = "127.0.0.1"
CFO_CALLBACK_PORT = 8765
_KEYRING_SERVICE = "GhostErase CFO.ai MCP"
_READ_TOOL_NAMES = frozenset({"inspect_pages", "inspect_table_blocks"})
_WORKSPACE_KEYS = ("org_id", "id", "slug")
_WORKSPACE_NAME_KEYS = ("name", "display_name", "label", "slug", "org_id", "id")
_WORKSPACE_CONTAINER_KEYS = ("orgs", "organizations", "workspaces")
_CALLBACK_HTML = (
    b"<!doctype html><html><head><meta charset=utf-8><title>GhostErase</title></head>"
    b"<body><p>CFO.ai authorization was received. You may close this window and return to "
    b"GhostErase.</p></body></html>"
)


class CfoIntegrationError(RuntimeError):
    def __init__(self, status: str, public_detail: str, category: str) -> None:
        super().__init__(category)
        self.status = status
        self.public_detail = public_detail
        self.category = category


class SecureCredentialStore:
    """Persist MCP OAuth material only in the current OS credential vault."""

    def __init__(self, backend: Any | None = None) -> None:
        self._backend = backend or keyring.get_keyring()

    @property
    def available(self) -> bool:
        module = type(self._backend).__module__
        system = platform.system()
        secure_modules = {
            "Windows": ("keyring.backends.Windows",),
            "Darwin": ("keyring.backends.macOS",),
            "Linux": (
                "keyring.backends.SecretService",
                "keyring.backends.kwallet",
            ),
        }
        return (
            self._backend.priority > 0
            and module.startswith(secure_modules.get(system, ("<unsupported>",)))
        )

    def _get(self, key: str) -> str | None:
        if not self.available:
            raise RuntimeError("Secure OS credential storage is unavailable.")
        try:
            return self._backend.get_password(_KEYRING_SERVICE, key)
        except Exception as error:
            raise RuntimeError("OS credential retrieval failed.") from error

    def _set(self, key: str, value: str) -> None:
        if not self.available:
            raise RuntimeError("Secure OS credential storage is unavailable.")
        try:
            self._backend.set_password(_KEYRING_SERVICE, key, value)
        except Exception as error:
            raise RuntimeError("OS credential storage failed.") from error

    async def _get_async(self, key: str) -> str | None:
        return await asyncio.to_thread(self._get, key)

    async def _set_async(self, key: str, value: str) -> None:
        await asyncio.to_thread(self._set, key, value)

    async def get_tokens(self) -> OAuthToken | None:
        value = await self._get_async("oauth_tokens")
        if value is None:
            return None
        try:
            return OAuthToken.model_validate_json(value)
        except ValidationError as error:
            raise RuntimeError("Stored OAuth token data is invalid.") from error

    async def set_tokens(self, tokens: OAuthToken) -> None:
        await self._set_async("oauth_tokens", tokens.model_dump_json())

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        value = await self._get_async("oauth_client_info")
        if value is None:
            return None
        try:
            return OAuthClientInformationFull.model_validate_json(value)
        except ValidationError as error:
            raise RuntimeError("Stored OAuth client registration is invalid.") from error

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        await self._set_async("oauth_client_info", client_info.model_dump_json())

    async def get_workspace_id(self) -> str | None:
        return await self._get_async("workspace_id")

    async def set_workspace_id(self, workspace_id: str) -> None:
        await self._set_async("workspace_id", workspace_id)

    async def clear(self) -> None:
        if not self.available:
            raise RuntimeError("Secure OS credential storage is unavailable.")
        for key in ("oauth_tokens", "oauth_client_info", "workspace_id"):
            try:
                await asyncio.to_thread(self._backend.delete_password, _KEYRING_SERVICE, key)
            except keyring.errors.PasswordDeleteError:
                continue
            except Exception as error:
                raise RuntimeError("OS credential removal failed.") from error


class CfoAiIntegration:
    def __init__(
        self,
        credential_store: SecureCredentialStore | None = None,
        server_url: str = CFO_SERVER_URL,
    ) -> None:
        self.credentials = credential_store or SecureCredentialStore()
        self.server_url = server_url
        self._lock = asyncio.Lock()
        self._callback_server: asyncio.AbstractServer | None = None
        self._callback_result: asyncio.Future[AuthorizationCodeResult] | None = None
        self._authorization_url: asyncio.Future[str] | None = None
        self._connect_task: asyncio.Task[None] | None = None
        self._state = "not_connected"
        self._detail = "Connect to authenticate with CFO.ai."
        self._tools: dict[str, Any] = {}
        self._workspaces: list[dict[str, str]] = []
        self._selected_workspace_id: str | None = None
        self._last_inspection: dict[str, Any] | None = None
        self._financial_result: dict[str, Any] | None = None

    async def status(self) -> dict[str, Any]:
        if not self.credentials.available:
            return self._status("integration_blocked", "A supported OS credential vault is unavailable.")
        if self._state == "not_connected":
            try:
                tokens, client_info = await asyncio.gather(
                    self.credentials.get_tokens(),
                    self.credentials.get_client_info(),
                )
            except Exception:
                return self._status("integration_blocked", "Secure credential storage could not be read.")
            if tokens is not None and client_info is not None:
                try:
                    self._selected_workspace_id = await self.credentials.get_workspace_id()
                except Exception:
                    return self._status("integration_blocked", "Secure workspace storage could not be read.")
                return self._status(
                    "connected_but_not_verified",
                    "Credentials are stored in the OS vault; a live workspace read has not been reverified.",
                )
        return self._status(self._state, self._detail)

    def _status(self, state: str, detail: str) -> dict[str, Any]:
        return {
            "state": state,
            "detail": detail,
            "server": self.server_url,
            "verified": state == "verified",
            "workspaces": list(self._workspaces),
            "selected_workspace_id": self._selected_workspace_id,
            "read_tools": [
                {
                    "name": name,
                    "description": getattr(tool, "description", None),
                    "input_schema": getattr(tool, "input_schema", {}),
                }
                for name, tool in self._tools.items()
            ],
            "last_inspection": self._last_inspection,
            "financial_result": self._financial_result,
        }

    async def start_connect(self) -> dict[str, Any]:
        if not self.credentials.available:
            self._state = "integration_blocked"
            self._detail = "A supported OS credential vault is unavailable; OAuth credentials will not be written to disk."
            raise CfoIntegrationError(self._state, self._detail, "SecureCredentialStoreUnavailable")
        async with self._lock:
            if self._connect_task is not None and not self._connect_task.done():
                raise CfoIntegrationError(
                    "awaiting_browser_authorization",
                    "An authorization flow is already in progress.",
                    "AuthorizationAlreadyPending",
                )
            loop = asyncio.get_running_loop()
            self._authorization_url = loop.create_future()
            self._callback_result = loop.create_future()
            self._workspaces = []
            self._tools = {}
            self._selected_workspace_id = None
            self._last_inspection = None
            self._financial_result = None
            self._state = "awaiting_browser_authorization"
            self._detail = "Waiting for CFO.ai authorization in your browser."
            try:
                self._callback_server = await asyncio.start_server(
                    self._handle_callback,
                    host=CFO_CALLBACK_HOST,
                    port=CFO_CALLBACK_PORT,
                    limit=8192,
                )
            except OSError as error:
                self._state = "integration_blocked"
                self._detail = "The documented loopback OAuth callback port is unavailable."
                raise CfoIntegrationError(
                    self._state, self._detail, "LoopbackCallbackUnavailable"
                ) from error
            self._connect_task = asyncio.create_task(self._finish_authorization())
            future = self._authorization_url
        try:
            authorization_url = await asyncio.wait_for(asyncio.shield(future), timeout=45)
        except asyncio.TimeoutError as error:
            self._state = "authentication_failed"
            self._detail = "CFO.ai authorization setup timed out before a browser URL was ready."
            if self._connect_task is not None and not self._connect_task.done():
                self._connect_task.cancel()
                try:
                    await self._connect_task
                except asyncio.CancelledError:
                    pass
            await self._close_callback_server()
            raise CfoIntegrationError(self._state, self._detail, "AuthorizationSetupTimeout") from error
        except CfoIntegrationError:
            raise
        except Exception as error:
            raise CfoIntegrationError(
                self._state,
                self._detail,
                type(error).__name__,
            ) from error
        return {
            "state": "awaiting_browser_authorization",
            "authorization_url": authorization_url,
            "callback_uri": CFO_REDIRECT_URI,
        }

    async def _redirect_handler(self, authorization_url: str) -> None:
        parts = urlsplit(authorization_url)
        if parts.scheme != "https" or not parts.hostname:
            raise CfoIntegrationError(
                "authentication_failed",
                "The authorization server returned an invalid browser URL.",
                "InvalidAuthorizationUrl",
            )
        if self._authorization_url is not None and not self._authorization_url.done():
            self._authorization_url.set_result(authorization_url)

    async def _callback_handler(self) -> AuthorizationCodeResult:
        if self._callback_result is None:
            raise CfoIntegrationError("authentication_failed", "OAuth callback is unavailable.", "CallbackUnavailable")
        try:
            return await asyncio.wait_for(asyncio.shield(self._callback_result), timeout=600)
        except asyncio.TimeoutError as error:
            raise CfoIntegrationError("authentication_failed", "Browser authorization timed out.", "CallbackTimeout") from error

    async def _handle_callback(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        code_result: AuthorizationCodeResult | None = None
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=10)
            method, target, _version = request_line.decode("ascii", errors="strict").strip().split(" ", 2)
            headers: dict[str, str] = {}
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=10)
                if line in (b"\r\n", b"\n", b""):
                    break
                key, value = line.decode("ascii", errors="strict").split(":", 1)
                headers[key.lower().strip()] = value.strip()
            parsed = urlsplit(target)
            if (
                method != "GET"
                or parsed.path != "/callback"
                or headers.get("host") not in {"127.0.0.1:8765", "localhost:8765"}
            ):
                raise ValueError("Invalid callback target.")
            params = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
            if "error" in params:
                raise CfoIntegrationError(
                    "authentication_failed",
                    "CFO.ai authorization was denied or could not be completed.",
                    "AuthorizationDenied",
                )
            if len(params.get("code", [])) != 1 or len(params.get("state", [])) != 1:
                raise ValueError("OAuth callback did not contain a single code and state.")
            issuer_values = params.get("iss", [])
            if len(issuer_values) > 1:
                raise ValueError("OAuth callback contained duplicate issuer values.")
            code_result = AuthorizationCodeResult(
                code=params["code"][0],
                state=params["state"][0],
                iss=issuer_values[0] if issuer_values else None,
            )
        except CfoIntegrationError as error:
            self._set_callback_exception(error)
        except Exception:
            self._set_callback_exception(
                CfoIntegrationError(
                    "authentication_failed",
                    "The loopback OAuth callback was malformed or invalid.",
                    "InvalidOAuthCallback",
                )
            )
        else:
            if self._callback_result is not None and not self._callback_result.done():
                self._callback_result.set_result(code_result)
        finally:
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/html; charset=utf-8\r\n"
                b"Cache-Control: no-store\r\n"
                b"Referrer-Policy: no-referrer\r\n"
                b"Content-Length: "
                + str(len(_CALLBACK_HTML)).encode("ascii")
                + b"\r\nConnection: close\r\n\r\n"
                + _CALLBACK_HTML
            )
            try:
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

    def _set_callback_exception(self, error: Exception) -> None:
        if self._callback_result is not None and not self._callback_result.done():
            self._callback_result.set_exception(error)

    async def _oauth_provider(self, interactive: bool) -> OAuthClientProvider:
        return OAuthClientProvider(
            server_url=self.server_url,
            client_metadata=OAuthClientMetadata(
                client_name="GhostErase local MVP",
                redirect_uris=[AnyUrl(CFO_REDIRECT_URI)],
                token_endpoint_auth_method="none",
                application_type="native",
                scope="openid profile email offline_access",
            ),
            storage=self.credentials,
            redirect_handler=self._redirect_handler if interactive else self._reauth_required,
            callback_handler=self._callback_handler if interactive else self._callback_reauth_required,
        )

    async def _reauth_required(self, _authorization_url: str) -> None:
        self._state = "reauthentication_required"
        self._detail = "CFO.ai authorization expired; select Connect to authenticate again."
        raise CfoIntegrationError(self._state, self._detail, "ReauthenticationRequired")

    async def _callback_reauth_required(self) -> AuthorizationCodeResult:
        raise CfoIntegrationError(
            "reauthentication_required",
            "CFO.ai authorization must be renewed through the Connect action.",
            "ReauthenticationRequired",
        )

    @asynccontextmanager
    async def _client(self, interactive: bool) -> AsyncIterator[Client]:
        oauth = await self._oauth_provider(interactive)
        async with httpx2.AsyncClient(
            auth=oauth,
            timeout=httpx2.Timeout(30.0, read=90.0),
        ) as http_client:
            transport = streamable_http_client(self.server_url, http_client=http_client)
            async with Client(transport, raise_exceptions=True) as client:
                yield client

    async def _finish_authorization(self) -> None:
        try:
            async with self._client(interactive=True) as client:
                tools = await self._discover_tools(client)
                if "list_orgs" not in tools:
                    raise CfoIntegrationError(
                        "integration_blocked",
                        "The authorized CFO.ai server did not expose the documented list_orgs tool.",
                        "WorkspaceToolUnavailable",
                    )
                self._tools = {name: tool for name, tool in tools.items() if name in _READ_TOOL_NAMES}
                workspace_result = await self._call_tool(client, "list_orgs", {}, tools=tools)
                self._workspaces = self._extract_workspaces(workspace_result)
                if not self._workspaces:
                    raise CfoIntegrationError(
                        "authentication_failed",
                        "CFO.ai authorization succeeded but returned no usable workspaces.",
                        "NoAuthorizedWorkspace",
                    )
                if len(self._workspaces) > 1:
                    self._state = "workspace_selection_required"
                    self._detail = "Select the authorized CFO.ai workspace to continue."
                    return
                await self._select_and_verify(client, self._workspaces[0]["id"], tools)
        except CfoIntegrationError as error:
            self._state = error.status
            self._detail = error.public_detail
            if self._authorization_url is not None and not self._authorization_url.done():
                self._authorization_url.set_exception(error)
        except OAuthRegistrationError as error:
            status_match = re.search(r"Registration failed:\s*(\d{3})", str(error))
            http_status = f" (HTTP {status_match.group(1)})" if status_match else ""
            self._state = "authentication_failed"
            self._detail = (
                f"CFO.ai OAuth client registration failed{http_status}; the response body was withheld. "
                "Check whether the fixed loopback callback is permitted."
            )
            if self._authorization_url is not None and not self._authorization_url.done():
                self._authorization_url.set_exception(
                    CfoIntegrationError(self._state, self._detail, "OAuthRegistrationRejected")
                )
        except OAuthTokenError:
            self._state = "authentication_failed"
            self._detail = "CFO.ai token exchange or refresh failed; response details and credentials were withheld."
            if self._authorization_url is not None and not self._authorization_url.done():
                self._authorization_url.set_exception(
                    CfoIntegrationError(self._state, self._detail, "OAuthTokenFailure")
                )
        except OAuthFlowError:
            self._state = "authentication_failed"
            self._detail = "CFO.ai OAuth discovery or validation failed; state, issuer, and response details were withheld."
            if self._authorization_url is not None and not self._authorization_url.done():
                self._authorization_url.set_exception(
                    CfoIntegrationError(self._state, self._detail, "OAuthFlowFailure")
                )
        except (httpx2.HTTPError, TimeoutError):
            self._state = "authentication_failed"
            self._detail = "CFO.ai authentication or MCP communication failed; reconnect and retry."
            if self._authorization_url is not None and not self._authorization_url.done():
                self._authorization_url.set_exception(
                    CfoIntegrationError(self._state, self._detail, "OAuthOrMCPFailure")
                )
        except Exception as error:
            self._state = "authentication_failed"
            self._detail = f"CFO.ai connection failed ({type(error).__name__}); no financial data was accepted."
            if self._authorization_url is not None and not self._authorization_url.done():
                self._authorization_url.set_exception(
                    CfoIntegrationError(self._state, self._detail, type(error).__name__)
                )
        finally:
            await self._close_callback_server()

    async def _close_callback_server(self) -> None:
        if self._callback_server is not None:
            self._callback_server.close()
            await self._callback_server.wait_closed()
            self._callback_server = None

    async def _discover_tools(self, client: Client) -> dict[str, Any]:
        found: dict[str, Any] = {}
        cursor: str | None = None
        for _ in range(10):
            page = await client.list_tools(cursor=cursor)
            for tool in page.tools:
                if tool.name in found:
                    raise CfoIntegrationError(
                        "integration_blocked",
                        "CFO.ai returned duplicate tool names; tool selection was stopped.",
                        "DuplicateToolNames",
                    )
                found[tool.name] = tool
            cursor = page.next_cursor
            if not cursor:
                break
        else:
            raise CfoIntegrationError(
                "integration_blocked",
                "CFO.ai tool discovery exceeded the safe pagination limit.",
                "ToolPaginationLimit",
            )
        return found

    @staticmethod
    def _extract_workspaces(result: dict[str, Any]) -> list[dict[str, str]]:
        candidate: Any = result.get("structured_content")
        if candidate is None:
            candidate = result.get("content")
        if isinstance(candidate, list):
            parsed_items: list[Any] = []
            for item in candidate:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    try:
                        parsed_items.append(json.loads(item["text"]))
                    except json.JSONDecodeError:
                        continue
            candidate = parsed_items[0] if len(parsed_items) == 1 else parsed_items
        if isinstance(candidate, dict):
            for key in _WORKSPACE_CONTAINER_KEYS:
                if isinstance(candidate.get(key), list):
                    candidate = candidate[key]
                    break
        if not isinstance(candidate, list):
            return []
        workspaces: list[dict[str, str]] = []
        for entry in candidate:
            if not isinstance(entry, dict):
                continue
            workspace_id = next(
                (entry.get(key) for key in _WORKSPACE_KEYS if isinstance(entry.get(key), str) and entry[key]),
                None,
            )
            if not workspace_id:
                continue
            name = next(
                (
                    entry.get(key)
                    for key in _WORKSPACE_NAME_KEYS
                    if isinstance(entry.get(key), str) and entry[key]
                ),
                workspace_id,
            )
            workspaces.append({"id": workspace_id[:256], "name": name[:256]})
        unique = {workspace["id"]: workspace for workspace in workspaces}
        return list(unique.values())

    async def select_workspace(self, workspace_id: str) -> dict[str, Any]:
        if self._state != "workspace_selection_required" or self._connect_task is None:
            raise CfoIntegrationError(
                "integration_blocked",
                "There is no pending CFO.ai workspace selection.",
                "WorkspaceSelectionUnavailable",
            )
        if workspace_id not in {item["id"] for item in self._workspaces}:
            raise CfoIntegrationError(
                "integration_blocked",
                "The requested workspace was not in the authorized CFO.ai response.",
                "UnknownWorkspace",
            )
        await self.credentials.set_workspace_id(workspace_id)
        async with self._client(interactive=False) as client:
            tools = await self._discover_tools(client)
            self._tools = {name: tool for name, tool in tools.items() if name in _READ_TOOL_NAMES}
            await self._select_and_verify(client, workspace_id, tools)
        return await self.status()

    async def _select_and_verify(
        self,
        client: Client,
        workspace_id: str,
        tools: dict[str, Any],
    ) -> None:
        await self.credentials.set_workspace_id(workspace_id)
        self._selected_workspace_id = workspace_id
        self._state = "connected_but_not_verified"
        self._detail = "Workspace selected; performing an actual read-only CFO.ai inspection."
        if "inspect_pages" not in tools:
            self._detail = "Connected to the selected workspace, but no documented inspect_pages tool was available."
            return
        arguments = self._initial_pages_arguments(tools["inspect_pages"])
        try:
            result = await self._call_tool(
                client,
                "inspect_pages",
                arguments,
                tools=tools,
                workspace_id=workspace_id,
            )
        except Exception as error:
            self._detail = (
                "Connected to the selected workspace, but its read-only page inspection did not succeed "
                f"({type(error).__name__})."
            )
            return
        self._last_inspection = {
            "tool": "inspect_pages",
            "result": result,
        }
        self._state = "verified"
        self._detail = "CFO.ai MCP authentication, workspace selection, and read-only page inspection succeeded."
        self._financial_result = {
            "status": "insufficient_financial_data",
            "detail": "Workspace access is verified, but a page listing alone does not establish financial-impact evidence. Load an authorized table inspection.",
        }

    @staticmethod
    def _initial_pages_arguments(tool: Any) -> dict[str, Any]:
        schema = getattr(tool, "input_schema", None)
        if not isinstance(schema, dict):
            raise CfoIntegrationError(
                "connected_but_not_verified",
                "The CFO.ai page-inspection tool returned no valid input schema.",
                "InvalidToolSchema",
            )
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise CfoIntegrationError(
                "connected_but_not_verified",
                "The CFO.ai page-inspection input schema is invalid.",
                "InvalidToolSchema",
            )
        list_action = properties.get("list")
        if isinstance(list_action, dict) and list_action.get("type") == "object":
            if list_action.get("required"):
                raise CfoIntegrationError(
                    "connected_but_not_verified",
                    "CFO.ai page listing requires additional user-specified parameters.",
                    "PageInspectionNeedsInput",
                )
            return {"list": {}}
        if not schema.get("required"):
            return {}
        raise CfoIntegrationError(
            "connected_but_not_verified",
            "CFO.ai page inspection requires input; no argument was guessed.",
            "PageInspectionNeedsInput",
        )

    async def read_financial_data(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._state != "verified" or not self._selected_workspace_id:
            raise CfoIntegrationError(
                "connected_but_not_verified",
                "Verify an authorized workspace before reading financial data.",
                "WorkspaceNotVerified",
            )
        if tool_name != "inspect_table_blocks":
            raise CfoIntegrationError(
                "integration_blocked",
                "The financial panel only permits the CFO.ai inspect_table_blocks read tool.",
                "ReadToolNotAllowed",
            )
        if not isinstance(arguments, dict):
            raise CfoIntegrationError(
                "integration_blocked",
                "Tool arguments must be a JSON object.",
                "InvalidToolArguments",
            )
        async with self._client(interactive=False) as client:
            tools = await self._discover_tools(client)
            result = await self._call_tool(
                client,
                tool_name,
                arguments,
                tools=tools,
                workspace_id=self._selected_workspace_id,
            )
        has_data = result.get("structured_content") not in (None, {}, []) or bool(result.get("content"))
        self._financial_result = {
            "status": "insufficient_financial_data",
            "tool": tool_name,
            "result": result,
            "detail": (
                "CFO.ai returned this actual response from the selected workspace. Its relevance to GhostErase cost or exposure has not been established; no impact figure is inferred."
                if has_data
                else "CFO.ai returned no table data for this inspection."
            ),
        }
        return await self.status()

    async def _call_tool(
        self,
        client: Client,
        tool_name: str,
        arguments: dict[str, Any],
        tools: dict[str, Any],
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        if tool_name != "list_orgs" and tool_name not in _READ_TOOL_NAMES:
            raise CfoIntegrationError(
                "integration_blocked",
                "The requested CFO.ai tool is not in GhostErase's read-only allowlist.",
                "ReadToolNotAllowed",
            )
        tool = tools.get(tool_name)
        if tool is None:
            raise CfoIntegrationError(
                "integration_blocked",
                "The requested read-only CFO.ai tool was not discovered from the live server.",
                "ReadToolUnavailable",
            )
        schema = getattr(tool, "input_schema", None)
        if not isinstance(schema, dict):
            raise CfoIntegrationError(
                "integration_blocked",
                "The discovered CFO.ai tool did not provide a valid JSON Schema.",
                "InvalidToolSchema",
            )
        call_arguments = dict(arguments)
        if workspace_id is not None:
            schema_properties = schema.get("properties", {})
            if not isinstance(schema_properties, dict) or "org_id" not in schema_properties:
                raise CfoIntegrationError(
                    "connected_but_not_verified",
                    "The live CFO.ai tool schema does not accept the selected org_id; no workspace-scoped read was attempted.",
                    "WorkspaceArgumentUnsupported",
                )
            if "org_id" in call_arguments and call_arguments["org_id"] != workspace_id:
                raise CfoIntegrationError(
                    "integration_blocked",
                    "A conflicting workspace argument was rejected.",
                    "WorkspaceArgumentMismatch",
                )
            call_arguments["org_id"] = workspace_id
        try:
            validate_json_schema(instance=call_arguments, schema=schema)
        except (JSONSchemaValidationError, SchemaError) as error:
            raise CfoIntegrationError(
                "integration_blocked",
                "Arguments did not match the schema discovered from CFO.ai.",
                "InvalidToolArguments",
            ) from error
        try:
            result = await client.call_tool(tool_name, arguments=call_arguments)
        except TimeoutError as error:
            raise CfoIntegrationError(
                "connected_but_not_verified",
                "CFO.ai read-only inspection timed out; no financial data was accepted.",
                "ReadToolTimeout",
            ) from error
        except httpx2.HTTPError as error:
            raise CfoIntegrationError(
                "connected_but_not_verified",
                "CFO.ai could not be reached for the read-only inspection.",
                "ReadToolTransportFailure",
            ) from error
        except Exception as error:
            raise CfoIntegrationError(
                "connected_but_not_verified",
                "CFO.ai read-only inspection failed; response details were withheld.",
                type(error).__name__,
            ) from error
        if result.is_error:
            raise CfoIntegrationError(
                "connected_but_not_verified",
                "CFO.ai rejected the read-only inspection.",
                "ReadToolFailed",
            )
        try:
            serializable = result.model_dump(mode="json", exclude_none=True)
            if not isinstance(serializable, dict):
                raise TypeError("MCP tool response must be a JSON object.")
            serialized = json.dumps(serializable, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise CfoIntegrationError(
                "connected_but_not_verified",
                "CFO.ai returned a malformed response that could not be safely displayed.",
                "MalformedToolResponse",
            ) from error
        if len(serialized.encode("utf-8")) > 250_000:
            raise CfoIntegrationError(
                "connected_but_not_verified",
                "CFO.ai returned a response too large for the financial panel.",
                "ReadResultTooLarge",
            )
        return serializable

    async def disconnect(self) -> dict[str, Any]:
        if self._connect_task is not None and not self._connect_task.done():
            self._connect_task.cancel()
            try:
                await self._connect_task
            except asyncio.CancelledError:
                pass
        await self.credentials.clear()
        await self._close_callback_server()
        self._state = "not_connected"
        self._detail = "CFO.ai credentials were removed from the OS vault."
        self._tools = {}
        self._workspaces = []
        self._selected_workspace_id = None
        self._last_inspection = None
        self._financial_result = None
        return await self.status()

    async def shutdown(self) -> None:
        if self._connect_task is not None and not self._connect_task.done():
            self._connect_task.cancel()
            try:
                await self._connect_task
            except asyncio.CancelledError:
                pass
        await self._close_callback_server()
