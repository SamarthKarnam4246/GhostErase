from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

from backend.app.integrations.cfo_ai import (
    CfoAiIntegration,
    CfoIntegrationError,
    SecureCredentialStore,
)
from mcp.client.auth import AuthorizationCodeResult


class MemoryCredentialBackend:
    __module__ = "keyring.backends.Windows"
    priority = 1

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.values:
            import keyring

            raise keyring.errors.PasswordDeleteError("not found")
        del self.values[(service, username)]


class FakeToolClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        return SimpleNamespace(
            is_error=False,
            model_dump=lambda **_kwargs: {
                "structured_content": {"rows": [{"name": "Payroll", "value": 1000}]}
            },
        )


class FakeWriter:
    def __init__(self) -> None:
        self.response = b""

    def write(self, data: bytes) -> None:
        self.response += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


def test_credentials_round_trip_through_abstract_secure_store(monkeypatch: pytest.MonkeyPatch) -> None:
    # MemoryCredentialBackend declares a Windows vault module; simulate that
    # platform so the secure-store precondition holds on any OS.
    monkeypatch.setattr("backend.app.integrations.cfo_ai.platform.system", lambda: "Windows")

    async def run() -> None:
        backend = MemoryCredentialBackend()
        store = SecureCredentialStore(backend=backend)
        assert store.available
        assert await store.get_tokens() is None
        await store.set_workspace_id("workspace-test")
        assert await store.get_workspace_id() == "workspace-test"
        await store.clear()
        assert await store.get_workspace_id() is None

    asyncio.run(run())


def test_insecure_keyring_backend_is_not_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    class PlaintextBackend:
        priority = 1

    monkeypatch.setattr("backend.app.integrations.cfo_ai.platform.system", lambda: "Windows")
    assert not SecureCredentialStore(backend=PlaintextBackend()).available


def test_callback_passes_oauth_state_and_issuer_unchanged() -> None:
    async def run() -> None:
        integration = CfoAiIntegration(credential_store=SecureCredentialStore(MemoryCredentialBackend()))
        integration._callback_result = asyncio.get_running_loop().create_future()
        reader = asyncio.StreamReader()
        reader.feed_data(
            b"GET /callback?code=synthetic-code&state=synthetic-state&iss=https%3A%2F%2Fissuer.example HTTP/1.1\r\n"
            b"Host: 127.0.0.1:8765\r\n\r\n"
        )
        writer = FakeWriter()
        await integration._handle_callback(reader, writer)
        result = integration._callback_result.result()
        assert isinstance(result, AuthorizationCodeResult)
        assert result.code == "synthetic-code"
        assert result.state == "synthetic-state"
        assert result.iss == "https://issuer.example"
        assert b"synthetic-code" not in writer.response
        assert b"synthetic-state" not in writer.response

    asyncio.run(run())


@pytest.mark.parametrize(
    "target",
    [
        b"GET /callback?code=a&state=b&state=c HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n\r\n",
        b"GET /callback?error=access_denied&state=b HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n\r\n",
        b"GET /callback?code=a&state=b HTTP/1.1\r\nHost: evil.example:8765\r\n\r\n",
    ],
)
def test_callback_rejects_duplicate_denied_or_nonlocal_requests(target: bytes) -> None:
    async def run() -> None:
        integration = CfoAiIntegration(credential_store=SecureCredentialStore(MemoryCredentialBackend()))
        integration._callback_result = asyncio.get_running_loop().create_future()
        reader = asyncio.StreamReader()
        reader.feed_data(target)
        await integration._handle_callback(reader, FakeWriter())
        with pytest.raises(CfoIntegrationError):
            integration._callback_result.result()

    asyncio.run(run())


def test_read_boundary_injects_selected_workspace_and_rejects_writes() -> None:
    async def run() -> None:
        integration = CfoAiIntegration(credential_store=SecureCredentialStore(MemoryCredentialBackend()))
        client = FakeToolClient()
        schema = {
            "type": "object",
            "properties": {
                "org_id": {"type": "string"},
                "read": {
                    "type": "object",
                    "properties": {"target": {"type": "string"}},
                    "required": ["target"],
                },
            },
            "required": ["read"],
        }
        tools = {
            "inspect_table_blocks": SimpleNamespace(
                name="inspect_table_blocks",
                input_schema=schema,
                description="Read financial table cells",
            ),
            "edit_table_blocks": SimpleNamespace(
                name="edit_table_blocks",
                input_schema={"type": "object"},
                description="Edit a model",
            ),
        }
        with pytest.raises(CfoIntegrationError) as error:
            await integration._call_tool(
                client, "edit_table_blocks", {}, tools, workspace_id="workspace-1"
            )
        assert error.value.category == "ReadToolNotAllowed"
        assert client.calls == []

        with pytest.raises(CfoIntegrationError) as invalid_arguments:
            await integration._call_tool(
                client,
                "inspect_table_blocks",
                {"read": {}},
                tools,
                workspace_id="workspace-1",
            )
        assert invalid_arguments.value.category == "InvalidToolArguments"
        assert client.calls == []

        result = await integration._call_tool(
            client,
            "inspect_table_blocks",
            {"read": {"target": "Operating Plan"}},
            tools,
            workspace_id="workspace-1",
        )
        assert client.calls == [
            (
                "inspect_table_blocks",
                {"read": {"target": "Operating Plan"}, "org_id": "workspace-1"},
            )
        ]
        assert result["structured_content"]["rows"][0]["value"] == 1000
        assert "access_token" not in str(result)

    asyncio.run(run())


def test_read_boundary_rejects_conflicting_workspace_and_unknown_tools() -> None:
    async def run() -> None:
        integration = CfoAiIntegration(credential_store=SecureCredentialStore(MemoryCredentialBackend()))
        client = FakeToolClient()
        tools = {
            "inspect_pages": SimpleNamespace(
                input_schema={
                    "type": "object",
                    "properties": {"org_id": {"type": "string"}, "list": {"type": "object"}},
                }
            )
        }
        with pytest.raises(CfoIntegrationError) as mismatch:
            await integration._call_tool(
                client,
                "inspect_pages",
                {"org_id": "other-workspace"},
                tools,
                workspace_id="selected-workspace",
            )
        assert mismatch.value.category == "WorkspaceArgumentMismatch"
        with pytest.raises(CfoIntegrationError) as denied:
            await integration._call_tool(client, "edit_pages", {}, tools)
        assert denied.value.category == "ReadToolNotAllowed"
        assert client.calls == []

    asyncio.run(run())


def test_tool_response_serialization_rejects_malformed_values_without_echoing_data() -> None:
    async def run() -> None:
        integration = CfoAiIntegration(credential_store=SecureCredentialStore(MemoryCredentialBackend()))
        secret_marker = f"synthetic-malformed-{uuid.uuid4().hex}"
        malformed_client = SimpleNamespace(
            call_tool=lambda *_args, **_kwargs: asyncio.sleep(
                0,
                result=SimpleNamespace(
                    is_error=False,
                    model_dump=lambda **_dump_kwargs: {
                        "structured_content": {"invalid_number": float("nan"), "marker": secret_marker}
                    },
                ),
            )
        )
        tool = SimpleNamespace(name="inspect_table_blocks", input_schema={"type": "object"})
        with pytest.raises(CfoIntegrationError) as error:
            await integration._call_tool(
                malformed_client,
                "inspect_table_blocks",
                {},
                {"inspect_table_blocks": tool},
            )
        assert error.value.category == "MalformedToolResponse"
        assert secret_marker not in str(error.value)

    asyncio.run(run())


def test_workspace_and_tool_failure_are_not_promoted_to_verified() -> None:
    async def run() -> None:
        integration = CfoAiIntegration(credential_store=SecureCredentialStore(MemoryCredentialBackend()))
        integration._workspaces = [{"id": "authorized", "name": "Finance"}]
        integration._state = "workspace_selection_required"
        integration._connect_task = asyncio.create_task(asyncio.sleep(0))

        with pytest.raises(CfoIntegrationError) as wrong_workspace:
            await integration.select_workspace("other")
        assert wrong_workspace.value.category == "UnknownWorkspace"

        integration._state = "verified"
        integration._selected_workspace_id = "authorized"
        with pytest.raises(CfoIntegrationError) as write_attempt:
            await integration.read_financial_data("edit_table_blocks", {})
        assert write_attempt.value.category == "ReadToolNotAllowed"
        assert integration._state == "verified"

    asyncio.run(run())


def test_missing_page_read_permission_does_not_verify_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    # MemoryCredentialBackend declares a Windows vault module; simulate that
    # platform so the secure-store precondition holds on any OS.
    monkeypatch.setattr("backend.app.integrations.cfo_ai.platform.system", lambda: "Windows")

    async def run() -> None:
        integration = CfoAiIntegration(credential_store=SecureCredentialStore(MemoryCredentialBackend()))
        await integration._select_and_verify(
            FakeToolClient(),
            "authorized-workspace",
            {"list_orgs": SimpleNamespace(input_schema={"type": "object"})},
        )
        assert integration._state == "connected_but_not_verified"
        assert "inspect_pages" in integration._detail
        assert integration._last_inspection is None

    asyncio.run(run())


def test_cfo_timeout_and_malformed_tool_errors_are_sanitized() -> None:
    async def run() -> None:
        integration = CfoAiIntegration(credential_store=SecureCredentialStore(MemoryCredentialBackend()))
        tool = SimpleNamespace(
            name="inspect_table_blocks",
            input_schema={"type": "object"},
        )
        tools = {"inspect_table_blocks": tool}
        marker = f"synthetic-cfo-error-{uuid.uuid4().hex}"

        class TimeoutClient:
            async def call_tool(self, *_args, **_kwargs):
                raise TimeoutError(marker)

        with pytest.raises(CfoIntegrationError) as timeout:
            await integration._call_tool(TimeoutClient(), "inspect_table_blocks", {}, tools)
        assert timeout.value.category == "ReadToolTimeout"
        assert marker not in str(timeout.value)

        class FailedClient:
            async def call_tool(self, *_args, **_kwargs):
                raise ValueError(marker)

        with pytest.raises(CfoIntegrationError) as failure:
            await integration._call_tool(FailedClient(), "inspect_table_blocks", {}, tools)
        assert failure.value.category == "ValueError"
        assert marker not in str(failure.value)

    asyncio.run(run())


def test_live_table_response_without_relevance_evidence_remains_insufficient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # MemoryCredentialBackend declares a Windows vault module; simulate that
    # platform so the secure-store precondition holds on any OS.
    monkeypatch.setattr("backend.app.integrations.cfo_ai.platform.system", lambda: "Windows")

    async def run() -> None:
        integration = CfoAiIntegration(credential_store=SecureCredentialStore(MemoryCredentialBackend()))
        integration._state = "verified"
        integration._selected_workspace_id = "authorized-workspace"
        client = FakeToolClient()
        tool = SimpleNamespace(
            name="inspect_table_blocks",
            input_schema={
                "type": "object",
                "properties": {"org_id": {"type": "string"}},
            },
            description="Synthetic isolated test tool",
        )

        async def discover_tools(_client):
            return {"inspect_table_blocks": tool}

        @asynccontextmanager
        async def fake_client(*, interactive):
            assert interactive is False
            yield client

        integration._discover_tools = discover_tools
        integration._client = fake_client
        status = await integration.read_financial_data("inspect_table_blocks", {})

        assert status["state"] == "verified"
        assert status["financial_result"]["status"] == "insufficient_financial_data"
        assert status["financial_result"]["result"]["structured_content"]["rows"][0]["value"] == 1000
        assert status["selected_workspace_id"] == "authorized-workspace"
        assert client.calls == [
            ("inspect_table_blocks", {"org_id": "authorized-workspace"})
        ]

    asyncio.run(run())


def test_workspace_list_parser_accepts_only_identified_entries() -> None:
    entries = CfoAiIntegration._extract_workspaces(
        {
            "structured_content": {
                "organizations": [
                    {"org_id": "org-1", "name": "Finance"},
                    {"slug": "org-2", "display_name": "Operations"},
                    {"name": "Missing ID"},
                    "invalid",
                ]
            }
        }
    )
    assert entries == [
        {"id": "org-1", "name": "Finance"},
        {"id": "org-2", "name": "Operations"},
    ]


def test_page_verification_arguments_are_derived_from_live_schema() -> None:
    assert CfoAiIntegration._initial_pages_arguments(
        SimpleNamespace(
            input_schema={
                "type": "object",
                "properties": {"org_id": {"type": "string"}, "list": {"type": "object"}},
                "required": ["list"],
            }
        )
    ) == {"list": {}}
    with pytest.raises(CfoIntegrationError) as missing_input:
        CfoAiIntegration._initial_pages_arguments(
            SimpleNamespace(
                input_schema={"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}
            )
        )
    assert missing_input.value.category == "PageInspectionNeedsInput"


def test_session_wrapped_integration_error_maps_to_sanitized_response() -> None:
    from fastapi import HTTPException

    from backend.app.main import _cfo_integration_leaf

    denied = CfoIntegrationError("integration_blocked", "Write tools are not allowed.", "ReadToolNotAllowed")
    assert _cfo_integration_leaf(denied) is denied

    wrapped = BaseExceptionGroup("session", [denied])
    assert _cfo_integration_leaf(wrapped) is denied

    nested = BaseExceptionGroup("outer", [BaseExceptionGroup("inner", [denied])])
    assert _cfo_integration_leaf(nested) is denied

    foreign = BaseExceptionGroup("session", [RuntimeError("synthetic transport failure")])
    assert _cfo_integration_leaf(foreign) is None

    invalid = CfoIntegrationError("integration_blocked", "Arguments did not match.", "InvalidToolArguments")
    assert _cfo_integration_leaf(BaseExceptionGroup("session", [invalid])) is invalid


def test_grouped_session_error_maps_to_sanitized_http_response(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from fastapi import HTTPException

    from backend.app import main

    async def grouped_failure(_tool_name: str, _arguments: dict):
        raise BaseExceptionGroup(
            "session",
            [CfoIntegrationError("integration_blocked", "Arguments did not match.", "InvalidToolArguments")],
        )

    monkeypatch.setattr(main.cfo_ai, "read_financial_data", grouped_failure)
    with pytest.raises(HTTPException) as response:
        asyncio.run(main.cfo_read(main.CfoReadPayload(tool_name="inspect_table_blocks", arguments={})))
    assert response.value.status_code == 409
    assert response.value.detail == "Arguments did not match."
