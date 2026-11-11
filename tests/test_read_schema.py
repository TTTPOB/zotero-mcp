import json
from datetime import timedelta

from mcp.shared.memory import create_connected_server_and_client_session

from zotero_mcp.server import build_server


async def test_read_tools_publish_models_and_input_constraints(library, zot):
    async with create_connected_server_and_client_session(
        build_server(library), read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        listing = await session.list_tools()
        tools = {tool.name: tool for tool in listing.tools}

        page_schema = tools["zotero_search_items"].outputSchema
        assert page_schema["title"] == "ItemPage"
        assert {"ItemPage", "ItemSummary", "ItemMetadata"} <= {
            page_schema["title"],
            *page_schema.get("$defs", {}),
        }
        items_schema = page_schema["properties"]["items"]["items"]["anyOf"]
        assert {"$ref": "#/$defs/ItemSummary"} in items_schema
        assert {"$ref": "#/$defs/ItemMetadata"} in items_schema
        assert page_schema["$defs"]["ItemSummary"]["properties"]["candidate_attachment_key"]

        metadata_schema = tools["zotero_get_item_metadata"].outputSchema
        assert metadata_schema["title"] == "ItemMetadataBatch"
        keys_schema = tools["zotero_get_item_metadata"].inputSchema["properties"]["item_keys"]
        assert keys_schema["minItems"] == 1
        assert keys_schema["maxItems"] == 50
        assert keys_schema["items"]["minLength"] == 8
        assert keys_schema["items"]["maxLength"] == 8
        assert keys_schema["items"]["pattern"] == r"^[A-Za-z0-9]{8}$"
        assert "item_key" not in tools["zotero_get_item_metadata"].inputSchema["properties"]
        search_input = tools["zotero_search_items"].inputSchema["properties"]
        assert search_input["limit"]["minimum"] == 1
        assert search_input["limit"]["maximum"] == 100
        assert search_input["offset"]["minimum"] == 0
        assert "query or at least one valid tag" in tools["zotero_search_items"].description


async def test_summary_metadata_raw_and_query_error_are_protocol_safe(library, zot):
    native = {
        "key": "ABCD1234",
        "version": 7,
        "library": {"type": "user", "id": 1},
        "links": {"self": {"href": "http://localhost/items/ABCD1234"}},
        "data": {"itemType": "book", "title": "Full title", "abstractNote": "Text"},
    }
    zot.top.return_value = [native]
    zot.items.return_value = [native]

    async with create_connected_server_and_client_session(
        build_server(library), read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        summary = await session.call_tool("zotero_get_recent", {"limit": 1})
        assert summary.isError is False
        assert "result" not in summary.structuredContent
        assert summary.structuredContent["items"][0]["title"] == "Full title"
        assert "match" not in summary.structuredContent["items"][0]

        metadata = await session.call_tool("zotero_get_item_metadata", {"item_keys": ["abcd1234"]})
        assert metadata.isError is False
        item = metadata.structuredContent["items"][0]["item"]
        assert item["key"] == "ABCD1234"
        assert item["data"]["abstractNote"] == "Text"

        raw = await session.call_tool(
            "zotero_get_item_metadata", {"item_keys": ["abcd1234"], "view": "raw"}
        )
        assert raw.isError is False
        assert raw.structuredContent["items"][0]["item"] == native

        raw_list = await session.call_tool("zotero_get_recent", {"limit": 1, "view": "raw"})
        assert raw_list.isError is False
        assert raw_list.structuredContent["items"] == [native]

        empty = await session.call_tool("zotero_search_items", {"query": "", "tags": []})
        assert empty.isError is True
        assert empty.structuredContent["error"]["code"] == "QUERY_OR_TAGS_REQUIRED"
        assert "result" not in empty.structuredContent
        assert json.loads(empty.content[0].text) == empty.structuredContent
