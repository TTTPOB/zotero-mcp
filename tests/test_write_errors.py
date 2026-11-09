"""Contract errors of the item and collection write tools."""

import pytest
from pyzotero import ResourceNotFoundError

from zotero_mcp.errors import NOT_FOUND, LibraryError

MISSING = "ZZZZZZZZ"


def test_update_item_on_a_missing_key_is_not_found(library, zot):
    zot.item.side_effect = ResourceNotFoundError("Not found")
    with pytest.raises(LibraryError) as caught:
        library.update_item(MISSING, 7, {"title": "new"})
    assert caught.value.code == NOT_FOUND
    assert MISSING in caught.value.message
    assert "search_items" in caught.value.message
    assert caught.value.details == {"operation": "update_item", "key": MISSING}
    # Never send the PATCH: Zotero turns it into a 400 or even creates the item.
    zot.update_item.assert_not_called()


def test_delete_item_on_a_missing_key_is_not_found(library, zot):
    zot.delete_item.side_effect = ResourceNotFoundError("Not found")
    with pytest.raises(LibraryError) as caught:
        library.delete_item(MISSING, 7)
    assert caught.value.code == NOT_FOUND
    assert MISSING in caught.value.message
    assert caught.value.details["key"] == MISSING


def test_update_collection_on_a_missing_key_is_not_found(library, zot):
    zot.collection.side_effect = ResourceNotFoundError("Not found")
    with pytest.raises(LibraryError) as caught:
        library.update_collection(MISSING, 7, name="new")
    assert caught.value.code == NOT_FOUND
    assert "get_collections" in caught.value.message
    # Never send the PUT: Zotero would create the collection.
    zot.update_collection.assert_not_called()


def test_delete_collection_on_a_missing_key_is_not_found(library, zot):
    zot.delete_collection.side_effect = ResourceNotFoundError("Not found")
    with pytest.raises(LibraryError) as caught:
        library.delete_collection(MISSING, 7)
    assert caught.value.code == NOT_FOUND
    assert caught.value.details == {"operation": "delete_collection", "key": MISSING}
