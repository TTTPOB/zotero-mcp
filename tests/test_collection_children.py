import copy

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from zotero_mcp.errors import CHILD_ITEM_NOT_ALLOWED, LibraryError
from zotero_mcp.server import build_server


@pytest.fixture
def mixed_items(zot, item):
    """A top-level paper plus a child attachment and a child note, as Zotero returns them."""
    parent = copy.deepcopy(item)
    parent["key"] = "PAPER001"
    parent["data"]["collections"] = []
    attachment = {
        "key": "PDF00001",
        "version": 4,
        "data": {
            "itemType": "attachment",
            "parentItem": "PAPER001",
            "title": "paper.pdf",
            "collections": [],
            "tags": [],
        },
    }
    note = {
        "key": "NOTE0001",
        "version": 5,
        "data": {
            "itemType": "note",
            "parentItem": "PAPER001",
            "note": "<p>x</p>",
            "collections": [],
            "tags": [],
        },
    }
    by_key = {entry["key"]: entry for entry in (parent, attachment, note)}
    zot.item.side_effect = lambda item_key: copy.deepcopy(by_key[item_key])
    return by_key


def test_batch_with_child_items_is_rejected_before_any_write(library, zot, mixed_items):
    with pytest.raises(LibraryError) as caught:
        library.manage_collection_items(
            "COLL0001", ["paper001", "PDF00001", "NOTE0001", "pdf00001"], "add"
        )

    error = caught.value
    assert error.code == CHILD_ITEM_NOT_ALLOWED
    assert not error.retryable
    assert error.details == {
        "children": [
            {"key": "PDF00001", "parent_key": "PAPER001"},
            {"key": "NOTE0001", "parent_key": "PAPER001"},
        ]
    }
    assert "PDF00001" in error.message and "NOTE0001" in error.message
    zot.update_item.assert_not_called()


def test_top_level_items_are_still_added(library, zot, mixed_items):
    result = library.manage_collection_items("COLL0001", ["PAPER001"], "add")

    assert result["status"] == "success"
    assert result["results"][0]["status"] == "success"
    zot.update_item.assert_called_once()


def test_removing_child_items_from_a_collection_is_not_blocked(library, zot, mixed_items):
    result = library.manage_collection_items("COLL0001", ["PDF00001"], "remove")

    assert result["results"][0]["status"] == "unchanged"
    zot.update_item.assert_not_called()


def test_tags_can_still_be_added_to_child_items(library, zot, mixed_items):
    result = library.batch_update_tags(["PDF00001"], add=["checked"])

    assert result["results"][0]["status"] == "success"
    zot.update_item.assert_called_once()


@pytest.mark.anyio
async def test_mcp_error_payload_lists_children(library, zot, mixed_items):
    async with create_connected_server_and_client_session(build_server(library)) as session:
        result = await session.call_tool(
            "zotero_manage_collection_items",
            {"collection_key": "COLL0001", "item_keys": ["PAPER001", "PDF00001"], "action": "add"},
        )

    assert result.isError
    assert result.structuredContent["status"] == "failed"
    error = result.structuredContent["error"]
    assert error["code"] == "CHILD_ITEM_NOT_ALLOWED"
    assert error["details"]["children"] == [{"key": "PDF00001", "parent_key": "PAPER001"}]
    zot.update_item.assert_not_called()
