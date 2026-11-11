"""Every write MCP tool against a real Zotero, each verified by reading the library back."""

from live_support import TEXT_NEEDLE, read_item


def keys(page):
    return [item["key"] for item in page["items"]]


def tag_names(mcp, key):
    return {t["tag"] for t in read_item(mcp, key)["data"]["tags"]}


def version_of(mcp, key):
    return read_item(mcp, key)["version"]


def test_add_items_creates_readable_items(mcp):
    result = mcp(
        "add_items",
        {
            "items": [
                {"itemType": "journalArticle", "title": "Live added one"},
                {"itemType": "book", "title": "Live added two", "tags": [{"tag": "live-added"}]},
            ]
        },
    )
    assert result["ok"] and result["status"] == "success"
    assert [entry["input_index"] for entry in result["results"]] == [0, 1]
    first, second = (entry["key"] for entry in result["results"])
    assert read_item(mcp, first)["data"]["title"] == "Live added one"
    assert read_item(mcp, second)["item_type"] == "book"
    assert tag_names(mcp, second) == {"live-added"}


def test_add_items_reports_invalid_entries(mcp):
    result = mcp(
        "add_items",
        {"items": [{"itemType": "journalArticle", "title": "Live ok"}, {"itemType": "nonsense"}]},
    )
    # A batch may partially succeed: the valid entry is written, the bad one reported.
    assert result["ok"] is False and result["status"] == "partial"
    good, bad = result["results"]
    assert (good["input_index"], good["status"]) == (0, "success")
    assert (bad["input_index"], bad["status"]) == (1, "failed")
    assert "nonsense" in bad["error"]["message"]
    assert read_item(mcp, good["key"])["data"]["title"] == "Live ok"


def test_update_item_changes_only_given_fields(mcp, new_item):
    key, version = new_item(date="2020")
    result = mcp(
        "update_item",
        {"item_key": key, "version": version, "changes": {"title": "Live renamed"}},
    )
    assert result["ok"] and result["key"] == key
    item = read_item(mcp, key)["data"]
    assert item["title"] == "Live renamed"
    assert item["date"] == "2020"


def test_update_item_with_stale_version_is_a_conflict(mcp, new_item):
    key, version = new_item()
    mcp("update_item", {"item_key": key, "version": version, "changes": {"title": "first"}})
    result = mcp(
        "update_item",
        {"item_key": key, "version": version, "changes": {"title": "second"}},
        error=True,
    )
    assert result["error"]["code"] == "VERSION_CONFLICT"
    assert read_item(mcp, key)["data"]["title"] == "first"


def test_delete_item_removes_it(mcp, new_item):
    key, version = new_item()
    result = mcp("delete_item", {"item_key": key, "version": version})
    assert result["ok"] and result["key"] == key
    missing = mcp("get_item_metadata", {"item_keys": [key]})["items"][0]
    assert missing["status"] == "error"
    assert missing["error"]["code"] == "NOT_FOUND"


def test_create_note_is_a_child_note_with_tags(mcp, new_item):
    parent, _ = new_item()
    result = mcp(
        "create_note",
        {"parent_key": parent, "text": "line one\nline <two>", "title": "Heading", "tags": ["n1"]},
    )
    note_key = result["results"][0]["key"]
    note = read_item(mcp, note_key)
    assert note["parent_key"] == parent
    html = note["data"]["note"]
    assert "<h1>Heading</h1>" in html and "&lt;two&gt;" in html
    assert tag_names(mcp, note_key) == {"n1"}
    assert keys(mcp("get_notes", {"parent_key": parent})) == [note_key]


def test_collection_lifecycle(mcp):
    root = mcp("create_collection", {"name": "Live lifecycle root"})["results"][0]["key"]
    other = mcp("create_collection", {"name": "Live lifecycle other"})["results"][0]["key"]
    sub = mcp("create_collection", {"name": "Live sub", "parent_collection": root})["results"][0][
        "key"
    ]

    def collection(key):
        return {c["key"]: c for c in mcp("get_collections", {"limit": 100})["items"]}[key]

    assert collection(sub)["data"]["parentCollection"] == root

    mcp(
        "update_collection",
        {"collection_key": sub, "version": collection(sub)["version"], "name": "Live renamed"},
    )
    assert collection(sub)["data"]["name"] == "Live renamed"
    assert collection(sub)["data"]["parentCollection"] == root

    mcp(
        "update_collection",
        {"collection_key": sub, "version": collection(sub)["version"], "parent_collection": other},
    )
    assert collection(sub)["data"]["parentCollection"] == other
    mcp(
        "update_collection",
        {"collection_key": sub, "version": collection(sub)["version"], "parent_collection": ""},
    )
    assert collection(sub)["data"]["parentCollection"] is False

    result = mcp(
        "delete_collection", {"collection_key": sub, "version": collection(sub)["version"]}
    )
    assert result["ok"]
    assert sub not in {c["key"] for c in mcp("get_collections", {"limit": 100})["items"]}


def test_batch_update_tags_adds_and_removes_preserving_others(mcp, new_item):
    first, _ = new_item(tags=[{"tag": "keep"}, {"tag": "drop"}])
    second, _ = new_item()
    result = mcp(
        "batch_update_tags", {"item_keys": [first, second], "add": ["added"], "remove": ["drop"]}
    )
    assert result["ok"] and sorted(entry["key"] for entry in result["results"] if entry["status"] == "success") == sorted([first, second])
    assert tag_names(mcp, first) == {"keep", "added"}
    assert tag_names(mcp, second) == {"added"}

    again = mcp("batch_update_tags", {"item_keys": [first], "add": ["added"]})
    assert again["results"][0]["status"] == "unchanged"


def test_manage_collection_items_add_and_remove_top_level_items(mcp, new_item):
    collection = mcp("create_collection", {"name": "Live membership"})["results"][0]["key"]
    first, _ = new_item()
    second, _ = new_item()
    added = mcp(
        "manage_collection_items",
        {"collection_key": collection, "item_keys": [first, second], "action": "add"},
    )
    assert added["ok"] and sorted(entry["key"] for entry in added["results"] if entry["status"] == "success") == sorted([first, second])
    assert set(keys(mcp("get_collection_items", {"collection_key": collection}))) == {
        first,
        second,
    }

    removed = mcp(
        "manage_collection_items",
        {"collection_key": collection, "item_keys": [first], "action": "remove"},
    )
    assert removed["results"][0]["key"] == first
    assert keys(mcp("get_collection_items", {"collection_key": collection})) == [second]
    # Removing membership never deletes the item.
    assert read_item(mcp, first)["key"] == first


def test_attach_file_stores_a_readable_copy(mcp, new_item, tmp_path):
    parent, _ = new_item()
    source = tmp_path / "attached.txt"
    source.write_text(f"{TEXT_NEEDLE} attached through MCP\n", encoding="utf-8")
    result = mcp("attach_file", {"parent_key": parent, "file_path": str(source), "title": "Mine"})
    assert result["ok"] and result["results"][0]["stage"] == "upload"
    attachment = result["key"]
    metadata = read_item(mcp, attachment)
    assert metadata["parent_key"] == parent
    assert metadata["data"]["title"] == "Mine"
    assert metadata["data"]["linkMode"] == "imported_file"
    chunk = mcp("read_attachment_text", {"attachment_key": attachment})
    assert chunk["content"] == source.read_text(encoding="utf-8")


def test_attach_markdown_creates_once_and_never_duplicates(mcp, new_item):
    parent, _ = new_item()
    body = "# Title\n\nfirst body"
    created = mcp("attach_markdown", {"parent_key": parent, "content": body})
    assert created["ok"]
    attachment = created["key"]
    assert mcp("read_attachment_text", {"attachment_key": attachment})["content"] == body

    same = mcp("attach_markdown", {"parent_key": parent, "content": body})
    assert same["ok"] and same["key"] == attachment
    assert same["results"][0]["status"] == "unchanged"

    different = mcp(
        "attach_markdown", {"parent_key": parent, "content": "# Title\n\nother"}, error=True
    )
    assert different["ok"] is False
    assert different["error"]["code"] == "ATTACHMENT_CONTENT_CONFLICT"

    children = mcp("get_item_children", {"item_key": parent})
    assert keys(children) == [attachment]
    assert mcp("read_attachment_text", {"attachment_key": attachment})["content"] == body


def collection_keys(mcp):
    return {c["key"] for c in mcp("get_collections", {"limit": 100})["items"]}


def test_writes_to_a_missing_key_are_not_found(mcp):
    missing = "ZZZZZZZZ"

    result = mcp(
        "update_item",
        {"item_key": missing, "version": 1, "changes": {"title": "ghost"}},
        error=True,
    )
    assert result["error"]["code"] == "NOT_FOUND"
    assert missing in result["error"]["message"]

    # Zotero would create an item under this key when the PATCH carries an itemType.
    result = mcp(
        "update_item",
        {
            "item_key": missing,
            "version": 1,
            "changes": {"itemType": "journalArticle", "title": "ghost"},
        },
        error=True,
    )
    assert result["error"]["code"] == "NOT_FOUND"
    gone = mcp("get_item_metadata", {"item_keys": [missing]})["items"][0]
    assert gone["status"] == "error" and gone["error"]["code"] == "NOT_FOUND"

    result = mcp("delete_item", {"item_key": missing, "version": 1}, error=True)
    assert result["error"]["code"] == "NOT_FOUND"

    # Zotero would create a collection under this key when it is PUT.
    result = mcp(
        "update_collection",
        {"collection_key": missing, "version": 1, "name": "ghost"},
        error=True,
    )
    assert result["error"]["code"] == "NOT_FOUND"
    assert missing not in collection_keys(mcp)

    result = mcp("delete_collection", {"collection_key": missing, "version": 1}, error=True)
    assert result["error"]["code"] == "NOT_FOUND"


def test_batch_with_every_entry_failed_is_an_mcp_error(mcp):
    # Nothing was written, so isError must be true; per-entry results stay readable.
    result = mcp(
        "add_items", {"items": [{"itemType": "nonsense"}, {"itemType": "alsonope"}]}, error=True
    )
    assert result["ok"] is False and result["status"] == "failed"
    assert [entry["status"] for entry in result["results"]] == ["failed", "failed"]

    result = mcp("create_note", {"parent_key": "ZZZZZZZZ", "text": "orphan"}, error=True)
    assert result["ok"] is False and result["status"] == "failed"
    assert result["results"][0]["status"] == "failed"

    result = mcp("batch_update_tags", {"item_keys": ["ZZZZZZZZ"], "add": ["x"]}, error=True)
    assert result["status"] == "failed"
    assert result["results"][0]["error"]["code"] == "NOT_FOUND"


def test_add_items_rejects_unknown_fields_before_writing(mcp):
    result = mcp(
        "add_items",
        {
            "items": [
                {"itemType": "journalArticle", "title": "Live unknown field ok"},
                {"itemType": "journalArticle", "title": "Live unknown field", "bogus": "x"},
            ]
        },
        error=True,
    )
    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert "bogus" in result["error"]["message"]
    found = mcp("search_items", {"query": "Live unknown field"})
    assert found["items"] == []


def test_version_conflict_reports_current_version_and_can_be_resubmitted(mcp, new_item):
    key, version = new_item()
    mcp("update_item", {"item_key": key, "version": version, "changes": {"title": "first"}})
    current = version_of(mcp, key)

    result = mcp(
        "update_item",
        {"item_key": key, "version": version, "changes": {"title": "second"}},
        error=True,
    )
    assert result["error"]["code"] == "VERSION_CONFLICT"
    assert result["error"]["details"]["current_version"] == current

    mcp(
        "update_item",
        {
            "item_key": key,
            "version": result["error"]["details"]["current_version"],
            "changes": {"title": "second"},
        },
    )
    assert read_item(mcp, key)["data"]["title"] == "second"

    deleted = mcp("delete_item", {"item_key": key, "version": version}, error=True)
    assert deleted["error"]["code"] == "VERSION_CONFLICT"
    assert deleted["error"]["details"]["current_version"] == version_of(mcp, key)


def test_collection_version_conflict_reports_current_version(mcp):
    key = mcp("create_collection", {"name": "Live conflict"})["results"][0]["key"]

    def current():
        return {c["key"]: c for c in mcp("get_collections", {"limit": 100})["items"]}[key][
            "version"
        ]

    stale = current()
    mcp("update_collection", {"collection_key": key, "version": stale, "name": "Live conflict 2"})
    latest = current()

    result = mcp(
        "update_collection",
        {"collection_key": key, "version": stale, "name": "Live conflict 3"},
        error=True,
    )
    assert result["error"]["code"] == "VERSION_CONFLICT"
    assert result["error"]["details"]["current_version"] == latest

    deleted = mcp("delete_collection", {"collection_key": key, "version": stale}, error=True)
    assert deleted["error"]["code"] == "VERSION_CONFLICT"
    assert deleted["error"]["details"]["current_version"] == latest


def test_update_item_returns_the_new_version_for_chained_updates(mcp, new_item):
    key, version = new_item(date="2020")
    first = mcp("update_item", {"item_key": key, "version": version, "changes": {"title": "v-one"}})
    assert first["version"] == first["results"][0]["version"] == version_of(mcp, key)
    assert first["version"] > version

    # No re-read between the two updates.
    second = mcp(
        "update_item", {"item_key": key, "version": first["version"], "changes": {"title": "v-two"}}
    )
    assert second["version"] == version_of(mcp, key) > first["version"]
    assert read_item(mcp, key)["data"]["title"] == "v-two"


def test_noop_update_returns_the_item_version_not_the_library_version(mcp, new_item):
    key, version = new_item(title="Live noop")
    other, other_version = new_item()
    # Move the library version past the item's version.
    mcp("update_item", {"item_key": other, "version": other_version, "changes": {"title": "bump"}})
    result = mcp(
        "update_item", {"item_key": key, "version": version, "changes": {"title": "Live noop"}}
    )
    assert result["version"] == version == version_of(mcp, key)


def test_update_collection_returns_the_new_version(mcp):
    key = mcp("create_collection", {"name": "Live version"})["results"][0]["key"]

    def current():
        return {c["key"]: c for c in mcp("get_collections", {"limit": 100})["items"]}[key][
            "version"
        ]

    result = mcp(
        "update_collection", {"collection_key": key, "version": current(), "name": "Live version 2"}
    )
    assert result["version"] == result["results"][0]["version"] == current()
    chained = mcp(
        "update_collection",
        {"collection_key": key, "version": result["version"], "name": "Live version 3"},
    )
    assert chained["version"] == current() > result["version"]
