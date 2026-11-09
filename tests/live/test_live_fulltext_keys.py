"""Fulltext tools given keys of the wrong kind, and attachments with no index yet."""

import pytest
from live_support import PDF_NEEDLE


def standalone_note(mcp):
    result = mcp("add_items", {"items": [{"itemType": "note", "note": "<p>standalone</p>"}]})
    return result["results"][0]["key"]


def test_find_item_fulltext_accepts_a_parent_key(mcp, seed):
    result = mcp("find_item_fulltext", {"attachment_key": seed.alpha, "query": PDF_NEEDLE})
    assert result["attachment_key"] == seed.pdf
    assert result["matches"], result


def test_find_item_fulltext_parent_without_attachments(mcp, new_item):
    key, _ = new_item()
    error = mcp("find_item_fulltext", {"attachment_key": key, "query": "x"}, error=True)["error"]
    assert error["code"] == "FULLTEXT_ATTACHMENT_NOT_FOUND"
    assert "Index" not in error["message"]


@pytest.mark.parametrize("tool", ["get_item_fulltext", "find_item_fulltext"])
def test_note_keys_are_rejected_as_notes(mcp, seed, tool):
    for key in (seed.note, standalone_note(mcp)):
        arguments = {"item_key": key} if tool == "get_item_fulltext" else {"attachment_key": key}
        if tool == "find_item_fulltext":
            arguments["query"] = "x"
        error = mcp(tool, arguments, error=True)["error"]
        assert error["code"] == "INVALID_ARGUMENT", error
        assert "get_notes" in error["message"]
        assert error["details"] == {"key": key, "item_type": "note"}


def test_get_item_fulltext_note_as_attachment_key(mcp, seed):
    arguments = {"item_key": seed.alpha, "attachment_key": seed.note}
    error = mcp("get_item_fulltext", arguments, error=True)["error"]
    assert error["code"] == "INVALID_ARGUMENT"
    assert error["details"]["item_type"] == "note"


@pytest.mark.parametrize("tool", ["get_item_fulltext", "find_item_fulltext"])
def test_collection_and_missing_keys(mcp, seed, tool):
    def call(key):
        arguments = {"item_key": key} if tool == "get_item_fulltext" else {"attachment_key": key}
        if tool == "find_item_fulltext":
            arguments["query"] = "x"
        return mcp(tool, arguments, error=True)["error"]

    collection = call(seed.parent)
    assert collection["code"] == "INVALID_ARGUMENT"
    assert collection["details"] == {"key": seed.parent, "kind": "collection"}
    missing = call("ZZZZZZZZ")
    assert missing["code"] == "NOT_FOUND"
    assert missing["details"] == {"key": "ZZZZZZZZ"}


def test_linked_url_attachment_is_not_indexable(mcp, seed):
    result = mcp(
        "add_items",
        {
            "items": [
                {
                    "itemType": "attachment",
                    "linkMode": "linked_url",
                    "title": "Live web link",
                    "url": "https://example.com/live",
                    "contentType": "text/html",
                    "parentItem": seed.beta,
                }
            ]
        },
    )
    key = result["results"][0]["key"]
    error = mcp("get_item_fulltext", {"item_key": seed.beta, "attachment_key": key}, error=True)[
        "error"
    ]
    assert error["code"] == "FULLTEXT_NOT_INDEXED"
    assert error["retryable"] is False
    assert error["details"]["reason"] == "not_indexable"


def test_recent_attachment_without_index_is_retryable(mcp, seed):
    # Zotero never indexes the API-uploaded text attachment, so it stays unindexed
    # and, as the seed was just added, looks like indexing that may still be queued.
    error = mcp(
        "get_item_fulltext", {"item_key": seed.beta, "attachment_key": seed.text}, error=True
    )["error"]
    assert error["code"] == "FULLTEXT_NOT_INDEXED"
    assert error["retryable"] is True
    assert error["details"] == {"attachment_key": seed.text, "reason": "indexing_pending"}
