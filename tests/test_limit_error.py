import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from zotero_mcp.server import build_server


@pytest.mark.anyio
@pytest.mark.parametrize("tool", ["zotero_get_tags", "zotero_search_items", "zotero_get_recent"])
@pytest.mark.parametrize("limit", [500, 0])
async def test_out_of_range_limit_explains_max_and_paging(library, zot, tool, limit):
    arguments = {"limit": limit}
    if tool == "zotero_search_items":
        arguments["query"] = "term"
    async with create_connected_server_and_client_session(build_server(library)) as session:
        result = await session.call_tool(tool, arguments)

    assert result.isError
    error = result.structuredContent["error"]
    assert error["code"] == "INVALID_ARGUMENT"
    assert "between 1 and 100" in error["message"]
    assert "next_offset" in error["message"]
    assert list(error["details"][0]["loc"]) == ["limit"]
