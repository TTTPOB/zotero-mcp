import json
from datetime import timedelta

import httpx2
from mcp.shared.memory import create_connected_server_and_client_session
from pyzotero import CouldNotReachURLError, PreConditionFailedError, ResourceNotFoundError

from zotero_mcp.errors import UNKNOWN_WRITE_RESULT, VERSION_CONFLICT, LibraryError, from_exception
from zotero_mcp.fulltext import FulltextError, text_revision
from zotero_mcp.server import build_server


def test_fulltext_error_is_a_library_error_without_losing_fields():
    error = FulltextError(
        "FULLTEXT_NOT_INDEXED",
        "No indexed text.",
        details={"attachment_key": "ATTACH01"},
    )
    assert isinstance(error, LibraryError)
    assert from_exception(error).code == "FULLTEXT_NOT_INDEXED"
    assert from_exception(error).details == {"attachment_key": "ATTACH01"}


def test_unwrap_stops_at_business_error():
    error = LibraryError(UNKNOWN_WRITE_RESULT, "inspect", details={"key": "NEW00001"})
    wrapped = RuntimeError("tool failed")
    wrapped.__cause__ = error
    assert from_exception(wrapped, write=True) is error


def test_unwrap_stops_at_pyzotero_error():
    error = PreConditionFailedError("stale")
    wrapped = RuntimeError("tool failed")
    wrapped.__cause__ = error
    assert from_exception(wrapped).code == VERSION_CONFLICT


async def test_protocol_read_connection_error_keeps_real_cause(library, zot):
    request = httpx2.Request("GET", "http://localhost:23119/api/items")
    cause = httpx2.ConnectError("Zotero stopped", request=request)
    error = CouldNotReachURLError("connection failed")
    error.__cause__ = cause
    zot.items.side_effect = error
    async with create_connected_server_and_client_session(
        build_server(library), read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        result = await session.call_tool("zotero_search_items", {"query": "text"})
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "CONNECTION_ERROR"
    assert json.loads(result.content[0].text) == result.structuredContent


async def test_protocol_write_412_keeps_version_conflict(library, zot):
    request = httpx2.Request("PATCH", "http://localhost:23119/api/items/ABCD1234")
    response = httpx2.Response(412, request=request, text="Item has changed")
    cause = httpx2.HTTPStatusError("412 precondition failed", request=request, response=response)
    error = PreConditionFailedError("stale")
    error.__cause__ = cause
    zot.update_item.side_effect = error
    async with create_connected_server_and_client_session(
        build_server(library), read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        result = await session.call_tool(
            "zotero_update_item",
            {"item_key": "ABCD1234", "version": 7, "changes": {"title": "new"}},
        )
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == VERSION_CONFLICT
    assert result.structuredContent["error"]["details"]["operation"] == "update_item"
    assert json.loads(result.content[0].text) == result.structuredContent


async def test_protocol_no_index_preserves_attachment_key(library, zot):
    zot.fulltext_item.side_effect = ResourceNotFoundError("unindexed")
    zot.item.side_effect = lambda item_key: {
        "key": item_key,
        "data": {"itemType": "attachment", "contentType": "application/pdf"},
    }
    async with create_connected_server_and_client_session(
        build_server(library), read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        result = await session.call_tool(
            "zotero_get_item_fulltext",
            {"item_key": "ABCD1234", "attachment_key": "ATTACH01"},
        )
    assert result.isError is True
    assert result.structuredContent["error"] == {
        "code": "FULLTEXT_NOT_INDEXED",
        "message": (
            "No indexed text is available for this attachment. If it was just added, Zotero "
            "may still be indexing it and a later retry can work; otherwise rebuild the index "
            "in Zotero (scanned PDFs without a text layer are never indexed)."
        ),
        "retryable": False,
        "details": {"attachment_key": "ATTACH01", "reason": "not_indexed"},
    }
    assert json.loads(result.content[0].text) == result.structuredContent


async def test_protocol_ambiguous_candidates_preserve_details(library, zot):
    zot.children.return_value = [
        {"key": "ATTACH01", "data": {"contentType": "application/pdf"}},
        {"key": "ATTACH02", "data": {"contentType": "application/pdf"}},
    ]
    async with create_connected_server_and_client_session(
        build_server(library), read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        result = await session.call_tool("zotero_get_item_fulltext", {"item_key": "ABCD1234"})
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "FULLTEXT_ATTACHMENT_AMBIGUOUS"
    assert result.structuredContent["error"]["details"]["candidate_count"] == 2
    assert [
        candidate["attachment_key"]
        for candidate in result.structuredContent["error"]["details"]["candidates"]
    ] == [
        "ATTACH01",
        "ATTACH02",
    ]
    assert json.loads(result.content[0].text) == result.structuredContent


async def test_protocol_revision_mismatch_preserves_details(library, zot):
    zot.fulltext_item.side_effect = [{"content": "old"}, {"content": "new"}]
    async with create_connected_server_and_client_session(
        build_server(library), read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        first = await session.call_tool(
            "zotero_get_item_fulltext",
            {"item_key": "ABCD1234", "attachment_key": "ATTACH01"},
        )
        result = await session.call_tool(
            "zotero_get_item_fulltext",
            {
                "item_key": "ABCD1234",
                "attachment_key": "ATTACH01",
                "text_revision": text_revision("old"),
            },
        )
    assert first.isError is False
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "FULLTEXT_REVISION_MISMATCH"
    assert result.structuredContent["error"]["details"]["expected"] == text_revision("old")
    assert result.structuredContent["error"]["details"]["actual"] == text_revision("new")
    assert json.loads(result.content[0].text) == result.structuredContent
