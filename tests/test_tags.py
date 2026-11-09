from types import SimpleNamespace

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from zotero_mcp.server import build_server


def test_get_tags_wraps_pyzotero_string_list(library, zot):
    # pyzotero's tags() returns plain tag-name strings.
    zot.tags.return_value = ["machine learning", "中文标签", "to-read"]
    zot.links = {}
    zot.request = SimpleNamespace(headers={"Total-Results": "3"})

    result = library.get_tags(limit=100)

    assert result == {
        "items": [{"tag": "machine learning"}, {"tag": "中文标签"}, {"tag": "to-read"}],
        "offset": 0,
        "next_offset": None,
        "total": 3,
    }
    zot.tags.assert_called_once_with(limit=100, start=0)


def test_get_tags_next_offset_uses_total(library, zot):
    zot.tags.return_value = ["a", "b"]
    zot.links = {}
    zot.request = SimpleNamespace(headers={"Total-Results": "5"})

    result = library.get_tags(limit=2, offset=2)

    assert result["items"] == [{"tag": "a"}, {"tag": "b"}]
    assert result["next_offset"] == 4
    zot.tags.assert_called_once_with(limit=2, start=2)


@pytest.mark.anyio
async def test_get_tags_over_mcp_matches_output_schema(library, zot):
    zot.tags.return_value = ["alpha", "beta"]
    zot.links = {}
    zot.request = SimpleNamespace(headers={"Total-Results": "2"})
    async with create_connected_server_and_client_session(build_server(library)) as session:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        schema = tools["zotero_get_tags"].outputSchema
        result = await session.call_tool("zotero_get_tags", {})

    assert "tag" in schema["$defs"]["TagEntry"]["properties"]
    assert not result.isError
    assert result.structuredContent["items"] == [{"tag": "alpha"}, {"tag": "beta"}]
