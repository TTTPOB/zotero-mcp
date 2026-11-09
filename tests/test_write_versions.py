"""update_item and update_collection return the new local version."""

import copy
import json
from datetime import timedelta

from mcp.shared.memory import create_connected_server_and_client_session

from zotero_mcp.server import build_server


def reads(zot, item, *versions):
    """zot.item answers with each version in turn (the last one repeats)."""
    remaining = list(versions)

    def read(item_key):
        version = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        return {**copy.deepcopy(item), "key": item_key, "version": version}

    zot.item.side_effect = read


def test_update_item_returns_the_item_version_read_back_after_the_write(library, zot, item):
    reads(zot, item, 7, 12)
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
    reads(zot, item, 7)
    assert library.update_item("ABCD1234", 7, {"title": "same"})["version"] == 7


def test_unreadable_new_version_is_null_not_version_plus_one(library, zot, item):
    first = [True]

    def read(item_key):
        if first:
            first.pop()
            return {**copy.deepcopy(item), "key": item_key}
        raise RuntimeError("cannot read")

    zot.item.side_effect = read
    result = library.update_item("ABCD1234", 7, {"title": "new"})
    assert result["ok"] is True
    assert result["version"] is None
    assert result["results"][0]["version"] is None


def test_update_collection_returns_the_new_version(library, zot):
    zot.collection.side_effect = [
        {"key": "COLL0001", "version": 7, "data": {"name": "Reading", "parentCollection": False}},
        {"key": "COLL0001", "version": 10, "data": {"name": "New", "parentCollection": False}},
    ]
    result = library.update_collection("COLL0001", 7, name="New")
    assert result["version"] == 10
    assert result["results"][0]["version"] == 10
    assert result["key"] == "COLL0001"


def test_batch_updates_keep_their_legacy_fields(library, zot):
    result = library.batch_update_tags(["ABCD1234"], add=["new"])
    assert result["updated"] == ["ABCD1234"]
    assert result["results"][0] == {"input_index": 0, "key": "ABCD1234", "status": "success"}


async def test_mcp_result_keeps_legacy_fields_and_adds_the_version(library, zot, item):
    reads(zot, item, 7, 8)
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
        assert legacy in content
    assert json.loads(result.content[0].text) == content
