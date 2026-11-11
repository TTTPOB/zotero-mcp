import copy
from unittest.mock import Mock

import pytest
from pyzotero import (
    LocalAPIKeyRequiredError,
    PreConditionFailedError,
    ResourceNotFoundError,
    ServerIDMismatchError,
)

from zotero_mcp.client import FulltextError, Library, key, pagination, write_result


def test_search_passes_filters_and_pagination(library, zot):
    result = library.search_items("中文", ["keep", "-read"], qmode="everything", limit=1, offset=2)
    zot.items.assert_called_once_with(
        q="中文",
        tag=["keep", "-read"],
        qmode="everything",
        limit=1,
        start=2,
    )
    assert result["next_offset"] == 3


@pytest.mark.parametrize("limit,offset", [(0, 0), (101, 0), (True, 0), (1, -1), (1, "0")])
def test_bad_pagination(limit, offset):
    with pytest.raises(ValueError):
        pagination(limit, offset)


@pytest.mark.parametrize("value", ["../items", "ABCD1234?q=x", "short", "汉字123456"])
def test_invalid_key(value):
    with pytest.raises(ValueError):
        key(value)


def test_blank_search_fails(library, zot):
    with pytest.raises(ValueError):
        library.search_items(" ")
    zot.items.assert_not_called()


def test_read_without_write_permission(library, zot):
    zot.local_api_key = None
    assert library.get_item_metadata(["abcd1234"])["items"][0]["item"]["key"] == "ABCD1234"
    with pytest.raises(ValueError, match="authorize"):
        library.create_note("ABCD1234", "note")
    zot.create_items.assert_not_called()
    zot.authorize_local.assert_not_called()


def test_cloud_factory_rejected():
    zot = Mock(local=False)
    library = Library(lambda: zot)
    with pytest.raises(ValueError, match="local API"):
        library.get_item_metadata(["ABCD1234"])
    zot.items.assert_not_called()
    zot.client.close.assert_called_once()


@pytest.mark.parametrize(
    "error,message",
    [
        (LocalAPIKeyRequiredError("expired"), "authorize"),
        (ServerIDMismatchError("changed"), "profile"),
        (PreConditionFailedError("stale"), "current local version"),
    ],
)
def test_write_errors_do_not_retry(library, zot, error, message):
    zot.update_item.side_effect = error
    with pytest.raises(ValueError, match=message):
        library.update_item("ABCD1234", 7, {"title": "new"})
    zot.update_item.assert_called_once()
    zot.authorize_local.assert_not_called()


def test_update_is_partial_and_uses_supplied_version(library, zot):
    changes = {"tags": [{"tag": "中文"}]}
    original = copy.deepcopy(changes)
    assert library.update_item("ABCD1234", 7, changes)["ok"]
    zot.update_item.assert_called_once_with({"key": "ABCD1234", "version": 7, **changes})
    assert changes == original


@pytest.mark.parametrize("changes", [{}, {"key": "OTHER123"}, {"version": 9}, {"data": {}}])
def test_invalid_patch(library, zot, changes):
    with pytest.raises(ValueError):
        library.update_item("ABCD1234", 7, changes)
    zot.update_item.assert_not_called()


def test_delete_uses_local_version(library, zot):
    library.delete_item("ABCD1234", 7)
    zot.delete_item.assert_called_once_with({"key": "ABCD1234", "version": 7})


@pytest.mark.parametrize("version", [-1, True, "7"])
def test_bad_version_never_writes(library, zot, version):
    with pytest.raises(ValueError):
        library.delete_item("ABCD1234", version)
    zot.delete_item.assert_not_called()


@pytest.mark.parametrize(
    "items", [[], [{}], [{"itemType": "book", "key": "ABCD1234"}], [{"itemType": "book"}] * 51]
)
def test_invalid_create_never_writes(library, zot, items):
    with pytest.raises(ValueError):
        library.add_items(items)
    zot.create_items.assert_not_called()


def test_partial_create_reports_both_outcomes(library, zot):
    zot.create_items.return_value = {
        "success": {"0": "NEW00001"},
        "failed": {"1": {"code": 400, "message": "invalid"}},
    }
    result = library.add_items([{"itemType": "book"}, {"itemType": "note"}])
    assert result["ok"] is False
    assert result["results"][0]["key"] == "NEW00001"
    assert result["results"][1]["error"]["code"] == "INVALID_ARGUMENT"
    zot.create_items.assert_called_once()


def test_unrecognized_write_response_is_not_success():
    with pytest.raises(RuntimeError):
        write_result({"error": "unknown"})


def test_note_is_child_and_escapes_plain_text(library, zot):
    library.create_note("ABCD1234", "a < b\n中文", title="A & B", tags=["tag"])
    payload = zot.create_items.call_args.args[0][0]
    assert payload["parentItem"] == "ABCD1234"
    assert payload["note"] == "<h1>A &amp; B</h1><p>a &lt; b<br/>中文</p>"
    assert payload["tags"] == [{"tag": "tag"}]


def test_note_html_preserved_when_requested(library, zot):
    library.create_note("ABCD1234", "<p><b>note</b></p>", as_html=True)
    assert zot.create_items.call_args.args[0][0]["note"] == "<p><b>note</b></p>"


def test_annotation_can_be_created_and_updated(library, zot):
    annotation = {
        "itemType": "annotation",
        "parentItem": "ATTACH01",
        "annotationType": "highlight",
        "annotationText": "quote",
        "annotationPosition": '{"pageIndex":0,"rects":[[1,2,3,4]]}',
    }
    library.add_items([annotation])
    zot.create_items.assert_called_with([annotation])
    library.update_item("ANNOT001", 7, {"annotationComment": "中文"})
    zot.update_item.assert_called_with(
        {"key": "ANNOT001", "version": 7, "annotationComment": "中文"}
    )


def test_collection_rename_preserves_parent(library, zot):
    library.update_collection("COLL0001", 7, name="New name")
    assert zot.update_collection.call_args.args[0]["parentCollection"] == "PARENT01"


def test_collection_move_to_root(library, zot):
    library.update_collection("COLL0001", 7, parent_collection="")
    assert zot.update_collection.call_args.args[0]["parentCollection"] is False


def test_tag_edit_preserves_unrelated_automatic_tag(library, zot):
    result = library.batch_update_tags(["ABCD1234"], add=["new", "new"])
    assert result["results"][0]["status"] == "success"
    assert zot.update_item.call_args.args[0] == {
        "key": "ABCD1234",
        "version": 7,
        "tags": [{"tag": "keep", "type": 1}, {"tag": "new"}],
    }


def test_noop_tag_edit_skips_write(library, zot):
    result = library.batch_update_tags(["ABCD1234"], add=["keep"])
    assert result["results"][0] == {
        "input_index": 0,
        "key": "ABCD1234",
        "status": "unchanged",
        "version": 7,
    }
    zot.update_item.assert_not_called()


def test_collection_membership_preserves_other_collections(library, zot):
    library.manage_collection_items("COLL0002", ["ABCD1234"], "add")
    assert zot.update_item.call_args.args[0]["collections"] == ["COLL0001", "COLL0002"]
    library.manage_collection_items("COLL0001", ["ABCD1234"], "remove")
    assert zot.update_item.call_args.args[0]["collections"] == []
    zot.delete_item.assert_not_called()


def test_fulltext_uses_index_and_character_paging(library, zot):
    zot.children.return_value = [
        {"key": "ATTACH01", "data": {"itemType": "attachment", "contentType": "application/pdf"}}
    ]
    zot.fulltext_item.side_effect = [
        ResourceNotFoundError("parent has no index"),
        {"content": "甲乙丙丁戊", "indexedPages": 2, "totalPages": 3},
    ]
    result = library.get_item_fulltext("ABCD1234", offset=1, max_characters=2)
    assert result["attachment_key"] == "ATTACH01"
    assert result["content"] == "乙丙"
    assert result["next_offset"] == 3
    assert result["index"]["indexedPages"] == 2


def test_multiple_attachments_are_not_probed_or_silently_replaced(library, zot):
    zot.fulltext_item.side_effect = ResourceNotFoundError("parent has no index")
    zot.children.return_value = [
        {"key": k, "data": {"itemType": "attachment"}} for k in ("ATTACH01", "ATTACH02")
    ]
    with pytest.raises(FulltextError) as caught:
        library.get_item_fulltext("ABCD1234")
    assert caught.value.code == "FULLTEXT_ATTACHMENT_AMBIGUOUS"
    assert [candidate["attachment_key"] for candidate in caught.value.details["candidates"]] == [
        "ATTACH01",
        "ATTACH02",
    ]
    zot.fulltext_item.assert_called_once_with("ABCD1234")


def test_no_text_is_actionable(library, zot):
    zot.fulltext_item.side_effect = ResourceNotFoundError("parent has no index")
    with pytest.raises(FulltextError) as caught:
        library.get_item_fulltext("ABCD1234")
    assert caught.value.code == "FULLTEXT_ATTACHMENT_NOT_FOUND"


def test_failed_query_state_cleared(library, zot):
    def fail(**kwargs):
        zot.url_params = {"itemType": "note", "format": "bibtex"}
        raise RuntimeError("failure")

    zot.items.side_effect = fail
    with pytest.raises(RuntimeError):
        library.search_items("q")
    assert zot.url_params is None
    assert zot.links is None
    assert zot.tag_data is False


def test_attachment_upload_failure_keeps_created_key(library, zot, tmp_path):
    path = tmp_path / "中文.pdf"
    path.write_bytes(b"%PDF-test")
    zot.upload_attachments.side_effect = RuntimeError("upload interrupted")
    result = library.attach_file("ABCD1234", str(path))
    assert result["ok"] is False
    assert result["key"] == "NEW00001"
    assert result["error"]["code"] == "UNKNOWN_WRITE_RESULT"
    assert "inspect" in result["error"]["message"]
    assert zot.create_items.call_args.args[0][0]["filename"] == "中文.pdf"


def test_attachment_invalid_path_never_creates(library, zot):
    with pytest.raises(ValueError):
        library.attach_file("ABCD1234", "relative.pdf")
    zot.create_items.assert_not_called()
