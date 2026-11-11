"""MCP schema and boundary tests for fulltext inputs."""

from datetime import timedelta

from mcp.shared.memory import create_connected_server_and_client_session

from zotero_mcp.server import build_server


async def test_fulltext_tools_expose_constrained_inputs_and_success(library, zot):
    zot.fulltext_item.return_value = {"content": "indexed text"}
    async with create_connected_server_and_client_session(
        build_server(library), read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        get_schema = tools["zotero_get_item_fulltext"].inputSchema["properties"]
        find_schema = tools["zotero_find_item_fulltext"].inputSchema["properties"]
        result = await session.call_tool(
            "zotero_get_item_fulltext",
            {
                "item_key": "ATTACH01",
                "offset": 0,
                "max_characters": 100,
            },
        )

    assert "attachment_key" not in get_schema
    assert "attachment_key" not in find_schema
    assert find_schema["item_key"]["minLength"] == 8
    assert get_schema["item_key"]["minLength"] == 8
    assert get_schema["item_key"]["maxLength"] == 8
    assert get_schema["offset"]["minimum"] == 0
    assert get_schema["max_characters"] == {
        "default": 20000,
        "description": "Maximum Unicode code points per chunk (1–50,000).",
        "maximum": 50000,
        "minimum": 1,
        "title": "Max Characters",
        "type": "integer",
    }
    assert find_schema["context_characters"]["minimum"] == 0
    assert find_schema["context_characters"]["maximum"] == 5000
    assert find_schema["max_matches"]["minimum"] == 1
    assert find_schema["max_matches"]["maximum"] == 100
    assert not result.isError
    assert result.structuredContent["attachment_key"] == "ATTACH01"


async def test_original_attachment_tools_expose_safe_schemas_and_annotations(library, zot):
    async with create_connected_server_and_client_session(build_server(library)) as session:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}

    read = tools["zotero_read_attachment_text"]
    write = tools["zotero_attach_markdown"]
    assert read.annotations.readOnlyHint is True
    assert read.annotations.destructiveHint is False
    assert write.annotations.readOnlyHint is False
    assert write.annotations.destructiveHint is False
    assert read.inputSchema["properties"]["max_characters"] == {
        "default": 50000,
        "description": "Maximum Unicode code points per chunk (1–50,000).",
        "maximum": 50000,
        "minimum": 1,
        "title": "Max Characters",
        "type": "integer",
    }
    assert set(write.inputSchema["required"]) == {"parent_key", "content"}
    assert write.inputSchema["properties"]["content"]["minLength"] == 1


async def test_fulltext_schema_rejects_bad_key_as_structured_error(library, zot):
    async with create_connected_server_and_client_session(
        build_server(library), read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        result = await session.call_tool(
            "zotero_find_item_fulltext",
            {"item_key": "bad", "query": "text"},
        )

    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "INVALID_ARGUMENT"
    zot.fulltext_item.assert_not_called()
