"""Streamable HTTP transport: unauthenticated, loopback-first, stdio unchanged."""

from datetime import timedelta
from unittest.mock import Mock, call

import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from zotero_mcp import cli, config
from zotero_mcp.server import READ_TOOLS, WRITE_TOOLS, build_server

BASE_URL = "http://127.0.0.1:8000"


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


async def test_http_session_serves_tools_without_authentication(library, zot):
    server = build_server(library)
    app = server.streamable_http_app()
    transport = httpx.ASGITransport(app=app)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
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
