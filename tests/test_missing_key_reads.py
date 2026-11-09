"""Reads that name a key Zotero does not know must fail, not return someone else's data."""

import pytest
from pyzotero import ResourceNotFoundError

from zotero_mcp.errors import NOT_FOUND, LibraryError

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
