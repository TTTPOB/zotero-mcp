"""Behavior that is wrong in the current code; fixed in stage 3.

Each test asserts the intended behavior and is marked xfail(strict=True), so the suite
stays green now and the marker must be removed together with the fix.
"""

import pytest


def keys(page):
    return [item["key"] for item in page["items"]]


def entries_with(details, *names):
    """Every dict nested in `details` that carries all of `names`."""
    found = []
    stack = [details]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            if all(name in value for name in names):
                found.append(value)
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return found


def test_get_tags_lists_library_tags(mcp, seed):
    page = mcp("get_tags", {"limit": 100})
    names = {entry["tag"] for entry in page["items"]}
    assert {"live-seed", "live-alpha", "live-note"} <= names
    assert isinstance(page["offset"], int)


@pytest.mark.xfail(strict=True, reason="fixed in stage 3")
def test_adding_child_items_to_a_collection_is_rejected_before_any_write(mcp, seed, new_item):
    # Zotero cannot place child items in a collection: it answers HTTP 500 and the
    # current code reports UNKNOWN_WRITE_RESULT after writing the earlier keys.
    collection = mcp("create_collection", {"name": "Live no children"})["results"][0]["key"]
    top, _ = new_item()

    result = mcp(
        "manage_collection_items",
        {"collection_key": collection, "item_keys": [top, seed.pdf, seed.note], "action": "add"},
        error=True,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "CHILD_ITEM_NOT_ALLOWED"
    rejected = {
        (entry["key"], entry["parent_key"])
        for entry in entries_with(result["error"].get("details"), "key", "parent_key")
    }
    assert rejected == {(seed.pdf, seed.alpha), (seed.note, seed.alpha)}
    # The whole batch is refused: the valid top-level item was not written either.
    assert mcp("get_collection_items", {"collection_key": collection})["items"] == []
    assert mcp("get_item_metadata", {"item_key": top})["data"]["collections"] == []
