"""Reads that name a key Zotero does not know must fail, not return someone else's data."""

from types import SimpleNamespace

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from pyzotero import ResourceNotFoundError

from zotero_mcp.errors import NOT_FOUND, LibraryError
from zotero_mcp.server import build_server


def child(key="NOTE0001"):
    return {"key": key, "version": 3, "data": {"itemType": "note", "parentItem": "ABCD1234"}}


# get_collection_items


def test_unknown_collection_is_not_found_and_lists_nothing(library, zot):
    zot.collection.side_effect = ResourceNotFoundError("missing")
    with pytest.raises(LibraryError) as caught:
        library.get_collection_items("ZZZZZZZZ")
    assert caught.value.code == NOT_FOUND
    assert "zotero_get_collections" in caught.value.message
    zot.collection.assert_called_once_with("ZZZZZZZZ")
    # Zotero would answer with the whole library; the request must not even be made.
    zot.collection_items_top.assert_not_called()


def test_existing_collection_is_listed(library, zot):
    zot.collection_items_top.return_value = []
    assert library.get_collection_items("coll0001")["items"] == []
    zot.collection.assert_called_once_with("COLL0001")
    zot.collection_items_top.assert_called_once_with("COLL0001", limit=25, start=0)


# get_item_children, get_notes (with parent), get_annotations

READS = [
    ("get_item_children", {"item_key": "ZZZZZZZZ"}),
    ("get_notes", {"parent_key": "ZZZZZZZZ"}),
    ("get_annotations", {"attachment_key": "ZZZZZZZZ"}),
]


@pytest.mark.parametrize("name,arguments", READS)
def test_empty_listing_of_unknown_key_is_not_found(library, zot, name, arguments):
    zot.children.return_value = []
    zot.item.side_effect = ResourceNotFoundError("missing")
    with pytest.raises(LibraryError) as caught:
        getattr(library, name)(**arguments)
    assert caught.value.code == NOT_FOUND
    assert "ZZZZZZZZ" in caught.value.message
    zot.item.assert_called_once_with("ZZZZZZZZ")


@pytest.mark.parametrize("name,arguments", READS)
def test_empty_listing_of_existing_key_keeps_its_pagination(library, zot, name, arguments):
    # The existence check is a second request; it must not replace the headers of the listing.
    listing = SimpleNamespace(headers={"Total-Results": "7"})
    zot.request = listing
    zot.children.return_value = []

    def lookup(item_key):
        zot.request = SimpleNamespace(headers={"Total-Results": "1"})
        return {"key": item_key, "data": {"itemType": "journalArticle"}}

    zot.item.side_effect = lookup
    result = getattr(library, name)(**arguments, offset=50)
    assert result["items"] == []
    assert result["total"] == 7
    zot.item.assert_called_once_with("ZZZZZZZZ")


@pytest.mark.parametrize("name,arguments", READS)
def test_listing_with_results_makes_no_extra_request(library, zot, name, arguments):
    zot.children.return_value = [child()]
    assert len(getattr(library, name)(**arguments)["items"]) == 1
    zot.item.assert_not_called()


def test_library_wide_notes_need_no_existence_check(library, zot):
    zot.items.return_value = []
    assert library.get_notes()["items"] == []
    zot.item.assert_not_called()
    zot.children.assert_not_called()


async def test_mcp_returns_not_found_for_unknown_children_key(library, zot):
    zot.children.return_value = []
    zot.item.side_effect = ResourceNotFoundError("missing")
    async with create_connected_server_and_client_session(build_server(library)) as session:
        result = await session.call_tool("zotero_get_item_children", {"item_key": "ZZZZZZZZ"})
    assert result.isError
    assert result.structuredContent["error"]["code"] == "NOT_FOUND"
