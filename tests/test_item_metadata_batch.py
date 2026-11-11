import copy

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from zotero_mcp.server import build_server


def _item(key, *, item_type="journalArticle", parent=None, title="Title"):
    data = {"itemType": item_type, "title": title, "tags": [], "collections": []}
    if parent:
        data["parentItem"] = parent
    return {"key": key, "version": 3, "library": {"type": "user", "id": 0}, "data": data}


PAPER = _item("PAPER001", title="A paper")
PDF = _item("PDF00001", item_type="attachment", parent="PAPER001", title="paper.pdf")
NOTE = _item("NOTE0001", item_type="note", parent="PAPER001")
OTHER = _item("OTHER001", title="Other")


def test_results_follow_input_order_with_duplicates_and_not_found(library, zot):
    zot.items.return_value = [OTHER, PAPER]
    keys = ["paper001", "MISSING1", "OTHER001", "PAPER001"]

    entries = library.get_item_metadata(keys)["items"]

    assert [e["index"] for e in entries] == [0, 1, 2, 3]
    assert [e["key"] for e in entries] == ["PAPER001", "MISSING1", "OTHER001", "PAPER001"]
    assert [e["status"] for e in entries] == ["ok", "error", "ok", "ok"]
    assert entries[0]["item"]["data"]["title"] == "A paper"
    assert entries[1]["error"]["code"] == "NOT_FOUND"
    assert "item" not in entries[1]
    assert "error" not in entries[0]


def test_child_items_are_readable_and_unrequested_children_are_dropped(library, zot):
    # Zotero returns the children of a requested parent in the same response.
    zot.items.return_value = [PAPER, PDF, NOTE]

    entries = library.get_item_metadata(["PAPER001"])["items"]
    assert [e["key"] for e in entries] == ["PAPER001"]

    zot.items.return_value = [PAPER, PDF, NOTE]
    entries = library.get_item_metadata(["NOTE0001", "PDF00001"])["items"]
    assert [e["status"] for e in entries] == ["ok", "ok"]
    assert entries[0]["item"]["parent_key"] == "PAPER001"
    assert entries[1]["item"]["item_type"] == "attachment"


def test_request_uses_items_endpoint_with_explicit_limit_and_all_pages(library, zot):
    zot.items.return_value = [PAPER]

    library.get_item_metadata(["PAPER001", "PDF00001"])

    zot.items.assert_called_once_with(itemKey="PAPER001,PDF00001", limit=100)
    zot.top.assert_not_called()
    zot.everything.assert_called_once()


def test_items_beyond_the_first_page_are_found(library, zot):
    first_page = [_item(f"CHILD{i:03d}", item_type="note", parent="PAPER001") for i in range(100)]
    zot.items.return_value = first_page
    # everything() follows the next links; the requested item is on page two.
    zot.everything.side_effect = lambda page: page + [copy.deepcopy(PAPER)]

    entries = library.get_item_metadata(["PAPER001"])["items"]

    assert entries[0]["status"] == "ok"


def test_fifty_keys_are_accepted(library, zot):
    keys = [f"KEY{i:05d}" for i in range(50)]
    zot.items.return_value = [_item(k) for k in keys]

    entries = library.get_item_metadata(keys)["items"]

    assert len(entries) == 50
    assert all(e["status"] == "ok" for e in entries)


@pytest.mark.anyio
async def test_mcp_rejects_old_item_key_and_oversized_batches(library, zot):
    async with create_connected_server_and_client_session(build_server(library)) as session:
        old = await session.call_tool("zotero_get_item_metadata", {"item_key": "ABCD1234"})
        empty = await session.call_tool("zotero_get_item_metadata", {"item_keys": []})
        many = await session.call_tool("zotero_get_item_metadata", {"item_keys": ["ABCD1234"] * 51})

    for result in (old, empty, many):
        assert result.isError
        assert result.structuredContent["error"]["code"] == "INVALID_ARGUMENT"
    zot.items.assert_not_called()
