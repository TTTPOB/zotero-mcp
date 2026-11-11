"""A write that failed entirely is an MCP error; a partial one is not."""

import json
from datetime import timedelta

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from pyzotero import InvalidItemFieldsError, ResourceNotFoundError

from zotero_mcp.errors import INVALID_ARGUMENT, LibraryError
from zotero_mcp.server import build_server


async def call(library, name, arguments):
    async with create_connected_server_and_client_session(
        build_server(library), read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        return await session.call_tool(f"zotero_{name}", arguments)


async def test_every_entry_failed_is_an_error_with_readable_results(library, zot):
    zot.create_items.return_value = {
        "success": {},
        "failed": {
            "0": {"code": 400, "message": "'nonsense' is not a valid itemType"},
            "1": {"code": 400, "message": "'other' is not a valid itemType"},
        },
    }
    result = await call(
        library, "add_items", {"items": [{"itemType": "nonsense"}, {"itemType": "other"}]}
    )
    assert result.isError is True
    assert result.structuredContent["ok"] is False
    assert result.structuredContent["status"] == "failed"
    assert [e["status"] for e in result.structuredContent["results"]] == ["failed", "failed"]
    assert result.structuredContent["results"][0]["error"]["code"] == 400
    assert json.loads(result.content[0].text) == result.structuredContent


async def test_partial_batch_is_not_an_error(library, zot):
    zot.create_items.return_value = {
        "success": {"0": "NEW00001"},
        "failed": {"1": {"code": 400, "message": "invalid"}},
    }
    result = await call(library, "add_items", {"items": [{"itemType": "book"}] * 2})
    assert result.isError is False
    assert result.structuredContent["status"] == "partial"


async def test_failed_create_note_is_an_error(library, zot):
    zot.create_items.return_value = {
        "success": {},
        "failed": {"0": {"code": 400, "message": "parentItem not found"}},
    }
    result = await call(library, "create_note", {"parent_key": "ZZZZZZZZ", "text": "orphan"})
    assert result.isError is True
    assert result.structuredContent["status"] == "failed"


async def test_successful_write_is_not_an_error(library, zot):
    result = await call(library, "delete_item", {"item_key": "ABCD1234", "version": 7})
    assert result.isError is False
    assert result.structuredContent["status"] == "success"


async def test_batch_whose_first_key_fails_is_failed_not_partial(library, zot):
    zot.item.side_effect = ResourceNotFoundError("Not found")
    result = await call(
        library, "batch_update_tags", {"item_keys": ["ZZZZZZZZ", "ABCD1234"], "add": ["x"]}
    )
    assert result.isError is True
    content = result.structuredContent
    assert content["status"] == "failed"
    assert [e["status"] for e in content["results"]] == ["failed", "pending"]
    assert content["results"][0]["error"]["code"] == "NOT_FOUND"
    assert content["results"][1]["key"] == "ABCD1234"


def test_batch_that_failed_after_a_success_stays_partial(library, zot):
    zot.update_item.side_effect = [True, ResourceNotFoundError("Not found")]
    result = library.batch_update_tags(["ABCD1234", "SECOND02"], add=["new"])
    assert result["status"] == "partial"


def test_add_items_rejects_unknown_fields_before_writing(library, zot):
    zot.check_items.side_effect = InvalidItemFieldsError("Invalid keys present in item 2: bogus")
    items = [{"itemType": "book"}, {"itemType": "book", "bogus": "x"}]
    with pytest.raises(LibraryError) as caught:
        library.add_items(items)
    assert caught.value.code == INVALID_ARGUMENT
    assert "bogus" in caught.value.message
    zot.create_items.assert_not_called()
