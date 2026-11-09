"""Regression tests for behavior that was wrong against a real Zotero (fixed in stage 3)."""

from live_support import read_item


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


def test_adding_child_items_to_a_collection_is_rejected_before_any_write(mcp, seed, new_item):
    # Zotero cannot place child items in a collection: it answers HTTP 500, which used
    # to surface as UNKNOWN_WRITE_RESULT after the earlier keys had been written.
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
    assert read_item(mcp, top)["data"]["collections"] == []


# Zotero answers reads about an unknown key with 200: the whole library for
# /collections/{key}/items/top, and [] for /items/{key}/children.


def error_code(result):
    return result["error"]["code"]


def test_get_collection_items_rejects_keys_that_are_not_collections(mcp, seed):
    for wrong in (seed.alpha, seed.pdf, "ZZZZZZZZ"):
        result = mcp("get_collection_items", {"collection_key": wrong}, error=True)
        assert error_code(result) == "NOT_FOUND", wrong
        assert "zotero_get_collections" in result["error"]["message"]


def test_get_collection_items_of_a_deleted_collection_is_not_found(mcp, seed):
    created = mcp("create_collection", {"name": "Live short-lived"})
    key = created["results"][0]["key"]
    version = mcp("get_collections", {"limit": 100})
    version = next(c["version"] for c in version["items"] if c["key"] == key)
    mcp("delete_collection", {"collection_key": key, "version": version})
    result = mcp("get_collection_items", {"collection_key": key}, error=True)
    assert error_code(result) == "NOT_FOUND"


def test_empty_collection_and_childless_item_are_still_empty_lists(mcp, seed, new_item):
    assert mcp("get_collection_items", {"collection_key": seed.child})["items"] == []
    lonely, _ = new_item()
    for name, arguments in (
        ("get_item_children", {"item_key": lonely}),
        ("get_notes", {"parent_key": lonely}),
        ("get_annotations", {"attachment_key": seed.text}),
    ):
        page = mcp(name, arguments)
        assert page["items"] == [], name
        assert page["total"] == 0, name


def test_child_listings_reject_unknown_keys(mcp, seed):
    for name, argument in (
        ("get_item_children", "item_key"),
        ("get_notes", "parent_key"),
        ("get_annotations", "attachment_key"),
    ):
        for wrong in ("ZZZZZZZZ", seed.parent):  # unknown key; a collection key
            result = mcp(name, {argument: wrong}, error=True)
            assert error_code(result) == "NOT_FOUND", (name, wrong)
            assert wrong in result["error"]["message"]


def test_listing_past_the_last_child_keeps_the_real_total(mcp, seed):
    page = mcp("get_item_children", {"item_key": seed.alpha, "offset": 50})
    assert page["items"] == []
    assert page["total"] == 2  # the PDF and the note, not the 1 of the existence check


def test_get_citation_items_tells_children_from_unknown_keys(mcp, seed):
    keys_in = [seed.note, seed.pdf, seed.annotation, seed.parent, "ZZZZZZZZ", seed.alpha]
    entries = mcp("get_citation_items", {"item_keys": keys_in})["items"]
    note, pdf, annotation, collection, missing, alpha = entries
    assert (note["error"]["code"], note["parent_key"]) == ("NOT_CITABLE", seed.alpha)
    assert (pdf["error"]["code"], pdf["parent_key"]) == ("NOT_CITABLE", seed.alpha)
    assert (annotation["error"]["code"], annotation["parent_key"]) == ("NOT_CITABLE", seed.pdf)
    for entry in (collection, missing):
        assert entry["error"]["code"] == "NOT_FOUND"
        assert entry["parent_key"] is None
    for entry in (note, pdf, annotation, collection, missing):
        assert "/items/top" not in entry["error"]["message"]
    assert alpha["status"] == "ok"
