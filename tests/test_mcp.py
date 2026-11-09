import sys
from datetime import timedelta

from mcp.shared.memory import create_connected_server_and_client_session

from zotero_mcp.server import READ_TOOLS, WRITE_TOOLS, build_server


async def test_every_tool_parameter_has_a_description(library):
    tools = await build_server(library).list_tools()
    for tool in tools:
        for name, schema in tool.inputSchema["properties"].items():
            assert schema.get("description", "").strip(), (tool.name, name)


async def test_protocol_discovery_read_write_and_shutdown(library, zot):
    server = build_server(library)
    async with create_connected_server_and_client_session(
        server, read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        listing = await session.list_tools()
        tools = {tool.name: tool for tool in listing.tools}
        assert len(tools) == len(READ_TOOLS) + len(WRITE_TOOLS)
        assert set(tools) == {f"zotero_{name}" for name in READ_TOOLS + WRITE_TOOLS}
        assert all("semantic" not in name for name in tools)
        assert tools["zotero_delete_item"].annotations.destructiveHint
        assert tools["zotero_search_items"].annotations.readOnlyHint
        required = tools["zotero_update_item"].inputSchema["required"]
        assert set(required) == {"item_key", "version", "changes"}

        read = await session.call_tool("zotero_get_item_metadata", {"item_key": "ABCD1234"})
        assert not read.isError
        assert read.structuredContent["key"] == "ABCD1234"

        write = await session.call_tool(
            "zotero_update_item",
            {
                "item_key": "ABCD1234",
                "version": 7,
                "changes": {"title": "中文更新"},
            },
        )
        assert not write.isError
        assert write.structuredContent["ok"] is True
        zot.update_item.assert_called_once()
    zot.client.close.assert_called_once()


async def test_protocol_write_without_permission_returns_error(library, zot):
    zot.local_api_key = None
    async with create_connected_server_and_client_session(build_server(library)) as session:
        result = await session.call_tool(
            "zotero_create_note", {"parent_key": "ABCD1234", "text": "note"}
        )
        assert result.isError
        assert "authorize" in result.content[0].text
    zot.create_items.assert_not_called()


async def test_protocol_rejects_invalid_enum_before_writing(library, zot):
    async with create_connected_server_and_client_session(build_server(library)) as session:
        result = await session.call_tool(
            "zotero_manage_collection_items",
            {
                "collection_key": "COLL0001",
                "item_keys": ["ABCD1234"],
                "action": "delete",
            },
        )
        assert result.isError
    zot.update_item.assert_not_called()


def test_no_model_runtime_imported():
    forbidden = {
        "chromadb",
        "torch",
        "transformers",
        "sentence_transformers",
        "onnxruntime",
        "openai",
        "fastmcp",
    }
    assert not forbidden & {name.split(".")[0] for name in sys.modules}
