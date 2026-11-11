"""update_item and update_collection return the new local version."""

import copy
import json
from datetime import timedelta

from mcp.shared.memory import create_connected_server_and_client_session
from write_support import item_versions

from zotero_mcp.client import write_result
from zotero_mcp.server import build_server


def test_create_results_use_the_native_entity_version(library, zot):
    native = {
        "successful": {
            "0": {"key": "NEW00001", "version": 19, "data": {"itemType": "book", "title": "new"}}
        },
        "success": {"0": "NEW00001"},
        "failed": {},
    }
    zot.create_items.return_value = native
    assert library.add_items([{"itemType": "book"}])["results"] == [
        {"input_index": 0, "key": "NEW00001", "status": "success", "version": 19}
    ]
    note = library.create_note("ABCD1234", "note")
    assert note["version"] == note["results"][0]["version"] == 19
    zot.item.assert_not_called()
    assert "version" not in write_result({"success": {"0": "NEW00001"}})["results"][0]


def test_update_item_returns_the_item_version_read_back_after_the_write(library, zot, item):
    zot.item.side_effect = item_versions(item, 7, 12)
    result = library.update_item("ABCD1234", 7, {"title": "new"})
    assert result["ok"] and result["status"] == "success"
    assert result["version"] == 12
    assert result["results"] == [
        {"input_index": 0, "key": "ABCD1234", "status": "success", "version": 12}
    ]


def test_version_does_not_come_from_the_response_header_or_a_guess(library, zot, item):
    # Last-Modified-Version is the library version: it differs from the item's own
    # version when the update changed nothing, so it must not be reported as the item's.
    zot.request.headers = {"Last-Modified-Version": "99"}
    zot.item.side_effect = item_versions(item, 7)
    assert library.update_item("ABCD1234", 7, {"title": "same"})["version"] == 7


def test_unreadable_new_version_is_absent_not_version_plus_one(library, zot, item):
    first = [True]

    def read(item_key):
        if first:
            first.pop()
            return {**copy.deepcopy(item), "key": item_key}
        raise RuntimeError("cannot read")

    zot.item.side_effect = read
    result = library.update_item("ABCD1234", 7, {"title": "new"})
    assert result["ok"] is True
    assert "version" not in result
    assert "version" not in result["results"][0]


def test_update_collection_returns_the_new_version(library, zot):
    zot.collection.side_effect = [
        {"key": "COLL0001", "version": 7, "data": {"name": "Reading", "parentCollection": False}},
        {"key": "COLL0001", "version": 10, "data": {"name": "New", "parentCollection": False}},
    ]
    result = library.update_collection("COLL0001", 7, name="New")
    assert result["version"] == 10
    assert result["results"][0]["version"] == 10
    assert result["key"] == "COLL0001"


def test_batch_updates_return_read_back_version_with_one_read_before_write(library, zot, item):
    zot.item.side_effect = item_versions(item, 7, 12)
    result = library.batch_update_tags(["ABCD1234"], add=["new"])
    assert result == {
        "ok": True,
        "status": "success",
        "results": [{"input_index": 0, "key": "ABCD1234", "status": "success", "version": 12}],
    }
    assert zot.item.call_count == 2
    zot.update_item.assert_called_once_with(
        {"key": "ABCD1234", "version": 7, "tags": [{"tag": "keep", "type": 1}, {"tag": "new"}]}
    )


def test_batch_unreadable_version_is_omitted_after_a_confirmed_write(library, zot, item):
    zot.item.side_effect = [item, RuntimeError("cannot read")]
    result = library.batch_update_tags(["ABCD1234"], add=["new"])
    assert result == {
        "ok": True,
        "status": "success",
        "results": [{"input_index": 0, "key": "ABCD1234", "status": "success"}],
    }


async def test_mcp_result_has_only_set_fields_and_the_version(library, zot, item):
    zot.item.side_effect = item_versions(item, 7, 8)
    async with create_connected_server_and_client_session(
        build_server(library), read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        result = await session.call_tool(
            "zotero_update_item",
            {"item_key": "ABCD1234", "version": 7, "changes": {"title": "new"}},
        )
    content = result.structuredContent
    assert result.isError is False
    assert content["version"] == content["results"][0]["version"] == 8
    for legacy in ("successful", "success", "unchanged", "failed", "unknown", "pending"):
        assert legacy not in content
    assert json.loads(result.content[0].text) == content
