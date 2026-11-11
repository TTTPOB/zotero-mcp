import json
from datetime import timedelta

from mcp.shared.memory import create_connected_server_and_client_session
from pyzotero import ResourceNotFoundError

from zotero_mcp.server import build_server


async def test_protocol_error_has_structured_content_and_is_error(library, zot):
    zot.local_api_key = None
    async with create_connected_server_and_client_session(
        build_server(library), read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        result = await session.call_tool(
            "zotero_create_note", {"parent_key": "ABCD1234", "text": "note"}
        )
    assert result.isError is True
    assert result.structuredContent["ok"] is False
    assert result.structuredContent["error"]["code"] == "UNAUTHORIZED"
    assert json.loads(result.content[0].text) == result.structuredContent


async def test_protocol_input_validation_uses_same_machine_error(library, zot):
    async with create_connected_server_and_client_session(
        build_server(library), read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        result = await session.call_tool(
            "zotero_manage_collection_items",
            {
                "collection_key": "COLL0001",
                "item_keys": ["ABCD1234"],
                "action": "delete",
            },
        )
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "INVALID_ARGUMENT"
    assert result.structuredContent["error"]["details"][0]["loc"] == ["action"]
    assert json.loads(result.content[0].text) == result.structuredContent
    zot.update_item.assert_not_called()


async def test_protocol_missing_metadata_key_is_a_per_key_error(library, zot):
    zot.items.return_value = []
    async with create_connected_server_and_client_session(
        build_server(library), read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        result = await session.call_tool("zotero_get_item_metadata", {"item_keys": ["ABCD1234"]})
    assert result.isError is False
    assert result.structuredContent["items"] == [
        {
            "index": 0,
            "key": "ABCD1234",
            "status": "error",
            "error": {"code": "NOT_FOUND", "message": "Item was not found in the library."},
        }
    ]


async def test_protocol_not_found_error_is_structured(library, zot):
    zot.children.side_effect = ResourceNotFoundError("missing")
    async with create_connected_server_and_client_session(
        build_server(library), read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        result = await session.call_tool("zotero_get_item_children", {"item_key": "ABCD1234"})
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "NOT_FOUND"
    assert json.loads(result.content[0].text) == result.structuredContent


async def test_protocol_success_and_error_fit_write_output_schema(library, zot):
    zot.update_item.return_value = True
    async with create_connected_server_and_client_session(
        build_server(library), read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        success = await session.call_tool(
            "zotero_update_item",
            {"item_key": "ABCD1234", "version": 7, "changes": {"title": "new"}},
        )
        invalid = await session.call_tool(
            "zotero_update_item",
            {"item_key": "bad", "version": 7, "changes": {"title": "new"}},
        )
    schema = tools["zotero_update_item"].outputSchema
    assert schema["required"] == ["ok"]
    assert set(schema["properties"]) == {"ok", "status", "results", "key", "version", "error"}
    assert schema["additionalProperties"] is False
    assert set(success.structuredContent) == {"ok", "status", "results", "key", "version"}
    assert set(success.structuredContent["results"][0]) == {"input_index", "status", "key", "version"}
    assert success.isError is False
    assert success.structuredContent["ok"] is True
    assert json.loads(success.content[0].text) == success.structuredContent
    assert invalid.isError is True
    assert invalid.structuredContent["ok"] is False
    assert invalid.structuredContent["error"]["code"] == "INVALID_ARGUMENT"


def test_create_annotations_are_non_destructive(library):
    server = build_server(library)
    import asyncio

    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    for name in (
        "zotero_add_items",
        "zotero_create_note",
        "zotero_create_collection",
        "zotero_attach_file",
    ):
        assert tools[name].annotations.destructiveHint is False
    assert tools["zotero_delete_item"].annotations.destructiveHint is True
