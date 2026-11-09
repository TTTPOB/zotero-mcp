"""Contract errors of the item and collection write tools."""

import copy

import pytest
from pyzotero import PreConditionFailedError, ResourceNotFoundError

from zotero_mcp.errors import NOT_FOUND, VERSION_CONFLICT, LibraryError

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


def item_versions(item, *versions):
    """zot.item answers with each version in turn (the last one repeats)."""
    remaining = list(versions)

    def read(item_key):
        version = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        return {**copy.deepcopy(item), "key": item_key, "version": version}

    return read


def test_update_item_with_a_stale_version_reports_the_current_version(library, zot):
    with pytest.raises(LibraryError) as caught:
        library.update_item("ABCD1234", 6, {"title": "new"})
    error = caught.value
    assert error.code == VERSION_CONFLICT
    assert error.details == {"operation": "update_item", "key": "ABCD1234", "current_version": 7}
    assert "version=7" in error.message
    assert "still apply" in error.message
    # A blind retry fails again and may overwrite someone else's change.
    assert error.retryable is False
    zot.update_item.assert_not_called()


def test_conflict_detected_by_zotero_reads_the_current_version(library, zot, item):
    zot.item.side_effect = item_versions(item, 7, 9)
    zot.update_item.side_effect = PreConditionFailedError("item version mismatch")
    with pytest.raises(LibraryError) as caught:
        library.update_item("ABCD1234", 7, {"title": "new"})
    assert caught.value.code == VERSION_CONFLICT
    assert caught.value.details["current_version"] == 9
    zot.update_item.assert_called_once()


def test_delete_item_conflict_reports_the_current_version(library, zot):
    zot.delete_item.side_effect = PreConditionFailedError("modified since specified version")
    with pytest.raises(LibraryError) as caught:
        library.delete_item("ABCD1234", 5)
    assert caught.value.code == VERSION_CONFLICT
    assert caught.value.details["current_version"] == 7
    assert "version=7" in caught.value.message


def test_update_collection_conflict_reports_the_current_version(library, zot):
    with pytest.raises(LibraryError) as caught:
        library.update_collection("COLL0001", 5, name="new")
    assert caught.value.code == VERSION_CONFLICT
    assert caught.value.details["current_version"] == 7
    zot.update_collection.assert_not_called()


def test_update_collection_conflict_found_by_zotero_reads_the_current_version(library, zot):
    zot.update_collection.side_effect = PreConditionFailedError("collection version mismatch")
    zot.collection.side_effect = [
        {"key": "COLL0001", "version": 7, "data": {"name": "Reading"}},
        {"key": "COLL0001", "version": 8, "data": {"name": "Reading"}},
    ]
    with pytest.raises(LibraryError) as caught:
        library.update_collection("COLL0001", 7, name="new")
    assert caught.value.details["current_version"] == 8


def test_delete_collection_conflict_reports_the_current_version(library, zot):
    zot.delete_collection.side_effect = PreConditionFailedError("modified since version")
    with pytest.raises(LibraryError) as caught:
        library.delete_collection("COLL0001", 5)
    assert caught.value.code == VERSION_CONFLICT
    assert caught.value.details["current_version"] == 7


def test_conflict_without_a_readable_version_still_reports_the_conflict(library, zot):
    zot.delete_item.side_effect = PreConditionFailedError("modified since specified version")
    zot.item.side_effect = RuntimeError("cannot read")
    with pytest.raises(LibraryError) as caught:
        library.delete_item("ABCD1234", 5)
    assert caught.value.code == VERSION_CONFLICT
    assert "current_version" not in caught.value.details
    assert "Re-read" in caught.value.message


def test_batch_conflict_entry_carries_the_current_version(library, zot, item):
    zot.item.side_effect = item_versions(item, 7, 7, 9)
    zot.update_item.side_effect = PreConditionFailedError("item version mismatch")
    result = library.batch_update_tags(["ABCD1234"], add=["new"])
    error = result["failed"]["ABCD1234"]
    assert error["code"] == VERSION_CONFLICT
    assert error["details"]["current_version"] == 9
