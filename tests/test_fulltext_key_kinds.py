"""Mock-only tests for fulltext tools given keys of the wrong kind or unindexed files."""

from datetime import UTC, datetime, timedelta

import pytest
from pyzotero import ResourceNotFoundError

from zotero_mcp.client import FulltextError
from zotero_mcp.errors import LibraryError
from zotero_mcp.fulltext import unindexed_attachment_error


def attachment(key, **data):
    return {"key": key, "data": {"itemType": "attachment", **data}}


def only_attachment_has_index(zot, indexed_key, content="found needle here"):
    def fulltext(key):
        if key == indexed_key:
            return {"content": content, "indexedPages": 1, "totalPages": 1}
        raise ResourceNotFoundError("not indexed")

    zot.fulltext_item.side_effect = fulltext


def test_find_accepts_a_parent_key_like_get_item_fulltext(library, zot):
    zot.children.return_value = [
        attachment("ATTACH01", contentType="application/pdf"),
        attachment("ATTACH02", contentType="text/html"),
    ]
    only_attachment_has_index(zot, "ATTACH01")

    result = library.find_item_fulltext("ABCD1234", "NEEDLE")

    assert result["attachment_key"] == "ATTACH01"
    assert result["total_matches"] == 1
    zot.children.assert_called_once_with("ABCD1234", itemType="attachment")


def test_find_parent_with_ambiguous_attachments_lists_candidates(library, zot):
    zot.children.return_value = [
        attachment(key, contentType="application/pdf") for key in ("ATTACH01", "ATTACH02")
    ]
    only_attachment_has_index(zot, "NOBODY00")

    with pytest.raises(FulltextError) as caught:
        library.find_item_fulltext("ABCD1234", "needle")

    assert caught.value.code == "FULLTEXT_ATTACHMENT_AMBIGUOUS"
    assert [c["attachment_key"] for c in caught.value.details["candidates"]] == [
        "ATTACH01",
        "ATTACH02",
    ]


def test_find_parent_whose_selected_attachment_is_unindexed(library, zot):
    zot.children.return_value = [attachment("ATTACH01", contentType="application/pdf")]
    only_attachment_has_index(zot, "NOBODY00")

    with pytest.raises(FulltextError) as caught:
        library.find_item_fulltext("ABCD1234", "needle")

    assert caught.value.code == "FULLTEXT_NOT_INDEXED"
    assert caught.value.details["attachment_key"] == "ATTACH01"


def test_find_real_attachment_key_needs_no_lookup(library, zot):
    only_attachment_has_index(zot, "ATTACH01")

    library.find_item_fulltext("ATTACH01", "needle")

    zot.item.assert_not_called()
    zot.children.assert_not_called()


@pytest.mark.parametrize("tool", ["get", "find"])
@pytest.mark.parametrize(
    ("item_type", "hint"), [("note", "get_notes"), ("annotation", "get_annotations")]
)
def test_note_and_annotation_keys_are_not_reported_as_unindexed(
    library, zot, tool, item_type, hint
):
    zot.fulltext_item.side_effect = ResourceNotFoundError("no index")
    zot.item.side_effect = lambda key: {
        "key": key,
        "data": {"itemType": item_type, "parentItem": "ABCD1234"},
    }

    with pytest.raises(LibraryError) as caught:
        if tool == "get":
            library.get_item_fulltext("NOTE0001")
        else:
            library.find_item_fulltext("NOTE0001", "needle")

    assert caught.value.code == "INVALID_ARGUMENT"
    assert item_type in caught.value.message
    assert hint in caught.value.message
    assert caught.value.details == {"key": "NOTE0001", "item_type": item_type}
    zot.children.assert_not_called()


def test_get_fulltext_rejects_a_note_given_as_attachment_key(library, zot):
    zot.fulltext_item.side_effect = ResourceNotFoundError("no index")
    zot.item.side_effect = lambda key: {"key": key, "data": {"itemType": "note"}}

    with pytest.raises(LibraryError) as caught:
        library.get_item_fulltext("ABCD1234", attachment_key="NOTE0001")

    assert caught.value.code == "INVALID_ARGUMENT"
    assert caught.value.details["item_type"] == "note"


def test_get_fulltext_rejects_a_parent_key_given_as_attachment_key(library, zot):
    zot.fulltext_item.side_effect = ResourceNotFoundError("no index")

    with pytest.raises(LibraryError) as caught:
        library.get_item_fulltext("ABCD1234", attachment_key="PARENT01")

    assert caught.value.code == "INVALID_ARGUMENT"
    assert caught.value.details == {"key": "PARENT01", "kind": "parent_item"}


@pytest.mark.parametrize("tool", ["get", "find"])
def test_unknown_key_is_not_found(library, zot, tool):
    zot.fulltext_item.side_effect = ResourceNotFoundError("missing")
    zot.item.side_effect = ResourceNotFoundError("missing")
    zot.collection.side_effect = ResourceNotFoundError("missing")

    with pytest.raises(LibraryError) as caught:
        if tool == "get":
            library.get_item_fulltext("MISSING1")
        else:
            library.find_item_fulltext("MISSING1", "needle")

    assert caught.value.code == "NOT_FOUND"
    assert caught.value.details == {"key": "MISSING1"}


@pytest.mark.parametrize("tool", ["get", "find"])
def test_collection_key_is_named_as_a_collection(library, zot, tool):
    zot.fulltext_item.side_effect = ResourceNotFoundError("missing")
    zot.item.side_effect = ResourceNotFoundError("missing")

    with pytest.raises(LibraryError) as caught:
        if tool == "get":
            library.get_item_fulltext("COLL0001")
        else:
            library.find_item_fulltext("COLL0001", "needle")

    assert caught.value.code == "INVALID_ARGUMENT"
    assert "collection" in caught.value.message
    assert caught.value.details == {"key": "COLL0001", "kind": "collection"}


def test_parent_without_attachments_does_not_ask_for_indexing(library, zot):
    with pytest.raises(FulltextError) as caught:
        library.get_item_fulltext("ABCD1234")

    assert caught.value.code == "FULLTEXT_ATTACHMENT_NOT_FOUND"
    assert "no attachments" in caught.value.message
    assert "Index" not in caught.value.message


NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def stamp(delta):
    return (NOW - delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_recent_pdf_without_index_is_retryable():
    error = unindexed_attachment_error(
        "ATTACH01",
        attachment(
            "ATTACH01", contentType="application/pdf", dateAdded=stamp(timedelta(minutes=1))
        ),
        now=NOW,
    )

    assert error.code == "FULLTEXT_NOT_INDEXED"
    assert error.retryable is True
    assert error.details == {"attachment_key": "ATTACH01", "reason": "indexing_pending"}
    assert "try again" in error.message


def test_old_pdf_without_index_is_not_retryable_and_says_to_reindex():
    error = unindexed_attachment_error(
        "ATTACH01",
        attachment("ATTACH01", contentType="application/pdf", dateAdded=stamp(timedelta(days=3))),
        now=NOW,
    )

    assert error.retryable is False
    assert error.details["reason"] == "not_indexed"
    assert "just added" in error.message
    assert "rebuild the index" in error.message


@pytest.mark.parametrize(
    "data",
    [
        {"linkMode": "linked_url", "contentType": "text/html"},
        {"linkMode": "imported_file", "contentType": "image/png"},
    ],
)
def test_unindexable_attachment_is_not_retryable_even_when_new(data):
    error = unindexed_attachment_error(
        "ATTACH01", attachment("ATTACH01", dateAdded=stamp(timedelta(seconds=5)), **data), now=NOW
    )

    assert error.retryable is False
    assert error.details["reason"] == "not_indexable"
    assert error.details["attachment_key"] == "ATTACH01"


@pytest.mark.parametrize("added", [None, "not a date", stamp(-timedelta(hours=1))])
def test_missing_or_implausible_date_is_not_treated_as_recent(added):
    data = {"contentType": "application/pdf"}
    if added is not None:
        data["dateAdded"] = added

    error = unindexed_attachment_error("ATTACH01", attachment("ATTACH01", **data), now=NOW)

    assert error.retryable is False
    assert error.details["reason"] == "not_indexed"
