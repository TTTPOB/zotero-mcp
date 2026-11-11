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
    assert summaries[1]["candidate_attachment_key"] == "PDF00001"
    assert "fulltext" not in summaries[1]
    assert "parent_key" not in summaries[2]


def test_summary_extracts_attachment_candidate_without_claiming_index():
    item = _native_item("PAPER001", title="Parent")
    item["links"]["attachment"] = {"href": "http://localhost:23119/api/users/0/items/PDF00001"}

    summary = project_items([item], "summary")[0]

    assert summary["candidate_attachment_key"] == "PDF00001"
    assert "fulltext" not in summary
    assert "match" not in summary


def test_attachment_candidate_does_not_treat_a_filename_as_a_key():
    item = _native_item("PAPER001", title="Parent")
    item["links"]["attachment"] = {
        "href": "http://localhost:23119/api/users/0/attachments/paperpdf"
    }

    assert "candidate_attachment_key" not in project_items([item], "summary")[0]


def test_summary_omits_missing_optional_fields():
    item = {"key": "PAPER001", "version": 1, "data": {"itemType": "book", "title": "Book"}}
    assert project_items([item], search=True) == [
        {"key": "PAPER001", "version": 1, "item_type": "book", "title": "Book"}
    ]


def test_metadata_is_semantic_and_raw_is_unchanged(library, zot):
    native = _native_item("PAPER001", abstract="Long abstract")
    zot.items.return_value = [native]

    metadata = library.get_item_metadata(["paper001"])["items"][0]["item"]
    raw = library.get_item_metadata(["PAPER001"], view="raw")["items"][0]["item"]

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


def test_collections_default_to_compact_projection_and_raw_is_unchanged(library, zot):
    root = {
        "key": "COLL0001",
        "version": 3,
        "data": {"name": "Reading", "parentCollection": False},
        "meta": {"numItems": 4},
        "links": {},
    }
    child = {
        "key": "COLL0002",
        "version": 5,
        "data": {"name": "Child", "parentCollection": "COLL0001"},
        "meta": {"numItems": 0},
    }
    zot.collections.return_value = [root, child]
    assert library.get_collections()["items"] == [
        {"key": "COLL0001", "version": 3, "name": "Reading", "item_count": 4},
        {
            "key": "COLL0002",
            "version": 5,
            "name": "Child",
            "parent_key": "COLL0001",
            "item_count": 0,
        },
    ]
    assert library.get_collections(view="raw")["items"] == [root, child]


def test_collection_items_lists_only_top_level_items(library, zot):
    paper = _native_item("PAPER001", title="Paper")
    standalone_note = _native_item("NOTE0002", item_type="note")
    child_note = _native_item("NOTE0001", item_type="note", parent_key="PAPER001")
    child_pdf = _native_item(
        "PDF00001", item_type="attachment", title="paper.pdf", parent_key="PAPER001"
    )
    # Zotero's non-top endpoint also returns the children of collection members.
    zot.collection_items.return_value = [paper, child_note, standalone_note, child_pdf]
    zot.collection_items_top.return_value = [paper, standalone_note]

    result = library.get_collection_items("coll0001", limit=25)

    assert [item["key"] for item in result["items"]] == ["PAPER001", "NOTE0002"]
    assert all("parent_key" not in item for item in result["items"])
    zot.collection_items_top.assert_called_once_with("COLL0001", limit=25, start=0)
    zot.collection_items.assert_not_called()


def test_lists_default_to_summary_and_keep_raw_escape_hatch(library, zot):
    native = _native_item("PAPER001", title="Full title")
    zot.top.return_value = [native]
    zot.collection_items_top.return_value = [native]

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
    zot.items.side_effect = [[parent, attachment], [parent]]

    result = library.search_items("body-only-term", qmode="everything", limit=25)

    assert "itemType" not in zot.items.call_args_list[0].kwargs
    assert [item["key"] for item in result["items"]] == ["PAPER001", "PDF00001"]
    assert result["items"][1]["matched_attachment_key"] == "PDF00001"
    assert result["items"][1]["parent_key"] == "PAPER001"
    assert "match_details_available" not in result["items"][1]


def test_parent_context_and_tag_scopes_share_one_batch_and_keep_search_pagination(library, zot):
    from types import SimpleNamespace

    hits = [
        _native_item("PDF00001", item_type="attachment", parent_key="PAPER001"),
        _native_item("PDF00002", item_type="attachment", parent_key="PAPER002", tags=["review"]),
        _native_item("PDF00003", item_type="attachment", parent_key="PAPER002"),
    ]
    parents = [
        _native_item("PAPER001", title="Parent", tags=["review"]),
        _native_item("PAPER002", title="Other"),
    ]

    def items(**kwargs):
        if "itemKey" in kwargs:
            assert kwargs == {"itemKey": "PAPER001,PAPER002", "limit": 100}
            zot.links = {}
            zot.request = SimpleNamespace(headers={"Total-Results": "2"})
            return parents
        assert "tag" not in kwargs
        zot.links = {"next": "http://localhost/items?start=3"}
        zot.request = SimpleNamespace(headers={"Total-Results": "20"})
        return hits

    zot.items.side_effect = items
    result = library.search_items(
        "body", tags=["review"], qmode="everything", tag_scope="parent", limit=3
    )
    assert [item["key"] for item in result["items"]] == ["PDF00001"]
    assert result["items"][0]["parent"] == {
        "title": "Parent",
        "creator_summary": "Wang",
        "year": 2024,
    }
    assert result["next_offset"] == 3
    assert result["total"] == 20
    assert zot.items.call_count == 2
    zot.item.assert_not_called()

    result = library.search_items(
        "body", tags=["review"], qmode="everything", tag_scope="either", limit=3
    )
    assert [item["key"] for item in result["items"]] == ["PDF00001", "PDF00002"]
    assert zot.items.call_count == 4


def test_note_summary_uses_first_nonempty_plain_text_line_in_every_list(library, zot):
    note = {
        "key": "NOTE0001",
        "version": 1,
        "data": {
            "itemType": "note",
            "note": "<div> </div><p>First &amp; useful</p><p>Second line</p>",
        },
    }
    zot.top.return_value = [note]
    zot.children.return_value = [note]
    zot.collection_items_top.return_value = [note]
    assert library.get_recent()["items"][0]["title"] == "First & useful"
    assert library.get_item_children("PAPER001")["items"][0]["title"] == "First & useful"
    assert library.get_collection_items("COLL0001")["items"][0]["title"] == "First & useful"
    note["data"]["note"] = "<p>" + "x" * 150 + "</p>"
    assert len(project_items([note])[0]["title"]) == 120


def test_explicit_item_type_is_sent_and_non_string_is_rejected(library, zot):
    library.search_items("term", qmode="everything", item_type=" -attachment ")
    assert zot.items.call_args.kwargs["itemType"] == "-attachment"

    zot.items.reset_mock()
    with pytest.raises(ValueError):
        library.search_items("term", item_type=[])
    zot.items.assert_not_called()


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
    assert summary_chars < raw_chars / 3
    assert summary_chars < raw_chars
