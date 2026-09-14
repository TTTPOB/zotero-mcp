"""Streamable HTTP transport: unauthenticated, loopback-first, stdio unchanged."""

import json
from datetime import timedelta
from unittest.mock import Mock, call

import httpx2 as http
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from zotero_mcp import cli, config
from zotero_mcp.server import READ_TOOLS, WRITE_TOOLS, build_server

BASE_URL = "http://127.0.0.1:8000"


def _message(response):
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line.removeprefix("data:").strip())
        raise AssertionError(f"SSE response did not contain a message: {response.text!r}")
    return response.json()


async def _post_rpc(client, payload, *, session_id=None):
    headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    if session_id is not None:
        headers["mcp-session-id"] = session_id
    response = await client.post("/mcp", headers=headers, json=payload)
    assert response.status_code == 200
    return response, _message(response)


def test_http_settings_default_to_loopback(monkeypatch):
    monkeypatch.delenv("ZOTERO_MCP_HTTP_HOST", raising=False)
    monkeypatch.delenv("ZOTERO_MCP_HTTP_PORT", raising=False)
    assert config.HttpSettings.from_env() == config.HttpSettings("127.0.0.1", 8000)


def test_http_settings_flags_win_over_environment(monkeypatch):
    monkeypatch.setenv("ZOTERO_MCP_HTTP_HOST", "0.0.0.0")
    monkeypatch.setenv("ZOTERO_MCP_HTTP_PORT", "9001")
    assert config.HttpSettings.from_env() == config.HttpSettings("0.0.0.0", 9001)
    assert config.HttpSettings.from_env("127.0.0.1", 9002) == config.HttpSettings("127.0.0.1", 9002)


@pytest.mark.parametrize(
    "host,port",
    [
        ("", 8000),
        ("  ", 8000),
        (0, 8000),
        ("127.0.0.1", 0),
        ("127.0.0.1", 65536),
        ("127.0.0.1", "8000"),
        ("127.0.0.1", True),
    ],
)
def test_invalid_http_settings(host, port):
    with pytest.raises(ValueError):
        config.HttpSettings(host, port)


def test_http_port_environment_must_be_numeric(monkeypatch):
    monkeypatch.setenv("ZOTERO_MCP_HTTP_PORT", "eight")
    with pytest.raises(ValueError, match="ZOTERO_MCP_HTTP_PORT"):
        config.HttpSettings.from_env()


@pytest.fixture
def fake_server(monkeypatch):
    server = Mock()
    server.settings.streamable_http_path = "/mcp"
    build = Mock(return_value=server)
    monkeypatch.setattr("zotero_mcp.server.build_server", build)
    return build, server


def test_serve_http_binds_loopback_and_reports_the_endpoint(fake_server, capsys):
    build, server = fake_server
    assert cli.main(["serve", "--transport", "http"]) == 0
    build.assert_called_once_with(host="127.0.0.1", port=8000)
    server.run.assert_called_once_with(transport="streamable-http")
    assert "http://127.0.0.1:8000/mcp" in capsys.readouterr().err


def test_serve_http_honors_host_and_port_flags(fake_server):
    build, server = fake_server
    argv = ["serve", "--transport", "http", "--host", "0.0.0.0", "--port", "9100"]
    assert cli.main(argv) == 0
    build.assert_called_once_with(host="0.0.0.0", port=9100)


def test_serve_stdio_is_still_the_default(fake_server):
    build, server = fake_server
    assert cli.main([]) == 0
    assert cli.main(["serve"]) == 0
    assert build.call_args_list == [call(), call()]
    assert server.run.call_args_list == [call(transport="stdio"), call(transport="stdio")]


def test_http_options_require_the_http_transport(fake_server, capsys):
    build, _ = fake_server
    assert cli.main(["serve", "--host", "0.0.0.0"]) == 1
    assert "http" in capsys.readouterr().err
    build.assert_not_called()


def test_out_of_range_http_port_is_rejected_before_binding(fake_server, capsys):
    build, _ = fake_server
    assert cli.main(["serve", "--transport", "http", "--port", "0"]) == 1
    assert "65535" in capsys.readouterr().err
    build.assert_not_called()


async def test_stateless_http_survives_requests_and_service_rebuild(library, zot):
    zot.client.close.reset_mock()
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "regression-test", "version": "1"},
        },
    }
    call = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "zotero_get_item_metadata", "arguments": {"item_key": "ABCD1234"}},
    }

    server = build_server(library)
    assert server.settings.stateless_http is True
    app = server.streamable_http_app()
    transport = http.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with http.AsyncClient(transport=transport, base_url=BASE_URL) as client:
            initialize_response, initialize_message = await _post_rpc(client, initialize)
            assert initialize_message["result"]["serverInfo"]["name"] == "Zotero Local"
            assert "mcp-session-id" not in initialize_response.headers
            assert initialize_response.headers["content-type"].startswith("text/event-stream")

            _, first_call = await _post_rpc(client, call)
            assert first_call["result"]["structuredContent"]["key"] == "ABCD1234"
            _, second_call = await _post_rpc(client, {**call, "id": 3})
            assert second_call["result"]["structuredContent"]["key"] == "ABCD1234"

            _, legacy_call = await _post_rpc(client, {**call, "id": 4}, session_id="legacy-session")
            assert legacy_call["result"]["structuredContent"]["key"] == "ABCD1234"
            zot.client.close.assert_not_called()

    zot.client.close.assert_called_once()

    rebuilt = build_server(library)
    rebuilt_app = rebuilt.streamable_http_app()
    rebuilt_transport = http.ASGITransport(app=rebuilt_app)
    async with rebuilt_app.router.lifespan_context(rebuilt_app):
        async with http.AsyncClient(transport=rebuilt_transport, base_url=BASE_URL) as client:
            _, rebuilt_call = await _post_rpc(client, {**call, "id": 5}, session_id="legacy-session")
            assert rebuilt_call["result"]["structuredContent"]["key"] == "ABCD1234"
            assert zot.client.close.call_count == 1

    assert zot.client.close.call_count == 2


async def test_http_session_serves_tools_without_authentication(library, zot):
    server = build_server(library)
    app = server.streamable_http_app()
    transport = http.ASGITransport(app=app)

    async with app.router.lifespan_context(app):
        async with http.AsyncClient(transport=transport, base_url=BASE_URL) as client:
            async with streamable_http_client(f"{BASE_URL}/mcp", http_client=client) as (
                read,
                write,
                _,
            ):
                async with ClientSession(
                    read, write, read_timeout_seconds=timedelta(seconds=10)
                ) as session:
                    # HTTP needs an explicit handshake; the in-memory test helper does it.
                    await session.initialize()
                    listing = await session.list_tools()
                    assert {tool.name for tool in listing.tools} == {
                        f"zotero_{name}" for name in READ_TOOLS + WRITE_TOOLS
                    }
                    read_result = await session.call_tool(
                        "zotero_get_item_metadata", {"item_key": "ABCD1234"}
                    )
                    assert not read_result.isError
                    assert read_result.structuredContent["key"] == "ABCD1234"
                    write_result = await session.call_tool(
                        "zotero_update_item",
                        {"item_key": "ABCD1234", "version": 7, "changes": {"title": "中文更新"}},
                    )
                    assert not write_result.isError
                    assert write_result.structuredContent["ok"] is True
    zot.update_item.assert_called_once()
