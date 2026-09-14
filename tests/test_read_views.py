import json

import pytest

from zotero_mcp.presentation import project_items


def _native_item(
    item_key: str,
    *,
    item_type: str = "journalArticle",
    title: str = "A title",
    parent_key: str | None = None,
    tags: list[str] | None = None,
    abstract: str | None = None,
    creators: list[dict] | None = None,
) -> dict:
    data = {
        "itemType": item_type,
        "title": title,
        "date": "2024",
        "creators": creators or [{"creatorType": "author", "lastName": "Wang"}],
        "tags": [{"tag": tag} for tag in (tags or [])],
        "collections": ["COLL0001"],
    }
    if parent_key:
        data["parentItem"] = parent_key
    if abstract is not None:
        data["abstractNote"] = abstract
    return {
        "key": item_key,
        "version": 3,
        "library": {"type": "user", "id": 1},
        "links": {"self": {"href": "http://localhost/items/" + item_key}},
        "data": data,
    }


def test_summary_keeps_native_order_titles_and_attachment_relation():
    items = [
        _native_item("PAPER001", title="A complete paper title"),
        _native_item("PDF00001", item_type="attachment", title="paper.pdf", parent_key="PAPER001"),
        _native_item("PDF00002", item_type="attachment", title="standalone.pdf"),
    ]

    summaries = project_items(items, "summary")

    assert [item["key"] for item in summaries] == ["PAPER001", "PDF00001", "PDF00002"]
    assert summaries[0]["title"] == "A complete paper title"
    assert "matched_attachment_key" not in summaries[1]
    assert "match" not in summaries[1]
    assert summaries[1]["parent_key"] == "PAPER001"
    assert summaries[1]["fulltext"] == {
        "read_key": "PDF00001",
        "candidate_attachment_key": "PDF00001",
        "index_state": "unknown",
    }
    assert summaries[2]["parent_key"] is None


def test_metadata_is_semantic_and_raw_is_unchanged(library, zot):
    native = _native_item("PAPER001", abstract="Long abstract")
    zot.item.side_effect = None
    zot.item.return_value = native

    metadata = library.get_item_metadata("paper001")
    raw = library.get_item_metadata("PAPER001", view="raw")

    assert metadata == {
        "key": "PAPER001",
        "version": 3,
        "item_type": "journalArticle",
        "parent_key": None,
        "data": native["data"],
    }
    assert raw == native
    assert "library" not in metadata
    assert "links" not in metadata


def test_lists_default_to_summary_and_keep_raw_escape_hatch(library, zot):
    native = _native_item("PAPER001", title="Full title")
    zot.top.return_value = [native]
    zot.collection_items.return_value = [native]

    recent = library.get_recent(limit=1)
    collection = library.get_collection_items("COLL0001", limit=1)
    raw_recent = library.get_recent(limit=1, view="raw")

    assert recent["items"][0]["title"] == "Full title"
    assert "abstractNote" not in recent["items"][0]
    assert collection["items"][0]["key"] == "PAPER001"
    assert raw_recent["items"] == [native]
    assert raw_recent["items"][0]["library"] == native["library"]


def test_everything_search_includes_native_attachment_hits(library, zot):
    parent = _native_item("PAPER001", title="Parent")
    attachment = _native_item(
        "PDF00001", item_type="attachment", title="paper.pdf", parent_key="PAPER001"
    )
    zot.items.return_value = [parent, attachment]

    result = library.search_items("body-only-term", qmode="everything", limit=25)

    assert "itemType" not in zot.items.call_args.kwargs
    assert [item["key"] for item in result["items"]] == ["PAPER001", "PDF00001"]
    assert result["items"][1]["matched_attachment_key"] == "PDF00001"
    assert result["items"][1]["parent_key"] == "PAPER001"
    assert result["items"][1]["match_details_available"] is False


def test_parent_tag_scope_is_explicit_and_bounded(library, zot):
    attachment = _native_item(
        "PDF00001", item_type="attachment", title="paper.pdf", parent_key="PAPER001"
    )
    parent = _native_item("PAPER001", title="Parent", tags=["review"])
    zot.items.return_value = [attachment]
    zot.item.side_effect = lambda item_key: parent if item_key == "PAPER001" else attachment

    result = library.search_items(
        "body-only-term",
        tags=["review"],
        qmode="everything",
        tag_scope="parent",
    )

    assert "tag" not in zot.items.call_args.kwargs
    assert [item["key"] for item in result["items"]] == ["PDF00001"]
    zot.item.assert_called_once_with("PAPER001")


@pytest.mark.parametrize(
    "kwargs",
    [{"view": []}, {"qmode": []}, {"tag_scope": []}],
)
def test_search_selector_constraints_are_value_errors(library, zot, kwargs):
    with pytest.raises(ValueError):
        library.search_items("query", **kwargs)
    zot.items.assert_not_called()


def test_read_view_selector_rejects_unhashable_values(library, zot):
    with pytest.raises(ValueError):
        library.get_recent(view=[])


def test_fixed_fixture_summary_size_is_reproducible():
    items = [
        _native_item(
            "PAPER001",
            title="A complete title that must remain intact for agent selection " * 4,
            abstract="Long abstract text " * 180,
            creators=[
                {"creatorType": "author", "firstName": f"Author{i}", "lastName": f"Surname{i}"}
                for i in range(8)
            ],
        ),
        _native_item(
            "PDF00001",
            item_type="attachment",
            title="An independent PDF with a complete filename and no parent relation",
        ),
        _native_item("PAPER002", title="Another complete title", abstract="Short abstract"),
    ]
    raw = project_items(items, "raw")
    summary = project_items(items, "summary")
    raw_chars = len(json.dumps(raw, ensure_ascii=False, indent=2))
    summary_chars = len(json.dumps(summary, ensure_ascii=False, indent=2))

    assert [item["key"] for item in summary] == ["PAPER001", "PDF00001", "PAPER002"]
    assert summary[0]["title"] == items[0]["data"]["title"]
    assert "matched_attachment_key" not in summary[1]
    assert raw_chars == 6167
    assert summary_chars == 1226
    assert summary_chars < raw_chars
